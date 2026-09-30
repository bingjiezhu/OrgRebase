from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.clock import SystemClock
from orgrebase.digest import sha256_digest
from orgrebase.domain import Approval, AuthorizationError, FreshnessError, IntegrityError
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_options, submit_change
from orgrebase.workspace.enterprise_binding import activate_binding, binding_revision
from orgrebase.workspace.execution import (
    DiscountMemoInputAssembler,
    DiscountMemoRenderer,
)
from orgrebase.workspace.formation import quote_discount_memo_profile
from orgrebase.workspace.models import (
    DeliverableApprovalDecision,
    DeliverableApprovalSet,
    DeliverableCandidateSet,
    DiscountMemoTaskLiterals,
    EnterpriseBinding,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.pricing import PricingPolicy, QuoteBasket
from orgrebase.workspace.runtime_revision import workspace_revision
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_priced_quote_pack import POLICY, priced_draft


def make_dual_service(root: Path, *, name: str = "dual"):
    sealed = root / f"{name}-pack"
    seal_enterprise_quote_pilot_pack(priced_draft(root / f"{name}-source"), sealed)
    runtime = load_enterprise_quote_pilot_pack(sealed)
    profile = quote_discount_memo_profile(runtime)
    service = WorkspaceService(
        store_path=root / f"{name}.sqlite",
        runtime_configuration=runtime,
        deliverable_set_profile=profile,
        review_duration_seconds=0,
    )
    members, as_actor = identity_directory(service)
    service._test_members = members
    service._test_as_actor = as_actor
    return service, runtime, profile


def identity_directory(service: WorkspaceService):
    service.identity_issuer = "https://identity.example"
    members = {
        item.owner_id: {"actor_id": item.owner_id, "roles": {"approver"}}
        for item in service.deliverable_set_profile.members
    }
    for owner_id in service.profile.governance.owner_refs:
        members.setdefault(owner_id, {"actor_id": owner_id, "roles": {"approver"}})
    members["executor:one"] = {"actor_id": "executor:one", "roles": {"executor"}}

    def verify(subject: str, actor_id: str, action: str) -> None:
        member = members.get(subject)
        allowed = {
            "approver": {"approve"},
            "executor": {"execute"},
        }
        if (
            member is None
            or member["actor_id"] != actor_id
            or not any(action in allowed[role] for role in member["roles"])
        ):
            raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

    service.verify_membership = verify
    service.authorize_workspace_subject = lambda subject: (
        None
        if subject in members
        else (_ for _ in ()).throw(AuthenticationError("AUTH_WORKSPACE_DENIED", 403))
    )

    @contextmanager
    def as_actor(actor_id: str):
        member = members[actor_id]
        token = request_principal.set(
            Principal(
                service.identity_issuer,
                actor_id,
                service.profile.organization_id,
                actor_id,
                frozenset(member["roles"]),
                int(time.time()) + 900,
            )
        )
        try:
            yield
        finally:
            request_principal.reset(token)

    return members, as_actor


def propose(service: WorkspaceService, event_id: str, slot_id: str, value: object):
    current = next(
        item for item in change_options(service)["fields"] if item["slot_id"] == slot_id
    )["current"]
    source_ref = f"source:controlled:{event_id}@v1"
    if slot_id in {"pricing_policy", "quote_basket"} and isinstance(value, dict):
        value = {**value, "source_ref": source_ref}
    submit_change(
        service,
        ChangeProposalInput(
            event_id=event_id,
            slot_id=slot_id,
            base_version=current["version"],
            base_digest=current["digest"],
            value=value,
            source_ref=source_ref,
        ),
    )
    return service.preview_change(event_id)


def required_owners(service: WorkspaceService, bundle) -> dict[str, str]:
    effects = {
        item.target_id: item.disposition.value
        for item in bundle.minimal_rebase_certificate.effects
    }
    return {
        member.owner_id: member.owner_id
        for member in service.deliverable_set_profile.members
        if effects[member.object_id] == "REBUILD"
    }


def approve_and_apply(service: WorkspaceService, event_id: str, bundle):
    source_approval, _ = approve_all(service, event_id, bundle)
    return service.apply_approved_change(
        event_id,
        approval_digest=source_approval["approval_digest"],
    )


def approve_all(service: WorkspaceService, event_id: str, bundle):
    if not hasattr(service, "_test_as_actor"):
        members, as_actor = identity_directory(service)
        service._test_members = members
        service._test_as_actor = as_actor
    with service._test_as_actor(service.change_owner[event_id]):
        source_approval = service.approve_change(
            event_id,
            actor_id=service.change_owner[event_id],
            preview_digest=bundle.preview.digest,
        )
    approved = None
    for owner_id in required_owners(service, bundle):
        with service._test_as_actor(owner_id):
            approved = service.approve_deliverable_set_change(
                event_id,
                owner_id=owner_id,
                actor_id=None,
                preview_digest=bundle.preview.digest,
            )
    assert approved is not None and approved["approval_set"]["status"] == "COMPLETE"
    return source_approval, approved


def manual_total(
    *, unit_price: str, quantity: int, discount_bps: int, tax_bps: int, minor_units: int
) -> tuple[str, str, str, str]:
    quantum = Decimal(1).scaleb(-minor_units)
    subtotal = (Decimal(unit_price) * quantity).quantize(quantum, rounding=ROUND_HALF_UP)
    discount = (subtotal * discount_bps / 10000).quantize(quantum, rounding=ROUND_HALF_UP)
    net = subtotal - discount
    tax = (net * tax_bps / 10000).quantize(quantum, rounding=ROUND_HALF_UP)
    pattern = f".{minor_units}f"
    return tuple(format(value, pattern) for value in (subtotal, discount, net, net + tax))


@pytest.mark.parametrize(
    "currency,unit_price,minor_units",
    [("USD", "100.00", 2), ("JPY", "100.50", 0), ("KWD", "100.0005", 3)],
)
def test_discount_memo_uses_independent_decimal_oracle(
    currency: str, unit_price: str, minor_units: int
) -> None:
    basket = QuoteBasket.model_validate(
        {
            "currency": currency,
            "items": (
                {
                    "line_id": "line-1",
                    "sku": "SKU-1",
                    "description": "Oracle vector",
                    "quantity": 1,
                    "unit_price": unit_price,
                },
            ),
            "source_ref": "source:basket@v1",
        }
    )
    before_policy = PricingPolicy.model_validate(
        {**POLICY, "discount_bps": 500, "tax_bps": 0, "source_ref": "source:policy@v1"}
    )
    after_policy = PricingPolicy.model_validate(
        {**POLICY, "discount_bps": 1000, "tax_bps": 0, "source_ref": "source:policy@v2"}
    )
    renderer = DiscountMemoRenderer()
    literals = DiscountMemoTaskLiterals(
        owner="human:finance-owner",
        customer_id="customer:test",
        quote_object_id="work:quote-test",
    )
    before = renderer.render(
        task_literals=literals,
        inputs=DiscountMemoInputAssembler.from_values(
            {
                "currency": currency,
                "quote_basket": basket.model_dump(mode="json"),
                "pricing_policy": before_policy.model_dump(mode="json"),
            }
        ),
    )
    after = renderer.render(
        task_literals=literals,
        inputs=DiscountMemoInputAssembler.from_values(
            {
                "currency": currency,
                "quote_basket": basket.model_dump(mode="json"),
                "pricing_policy": after_policy.model_dump(mode="json"),
            }
        ),
        change_set_ref="changeset:discount@r1",
        change_set_digest="sha256:" + "1" * 64,
        previous_pricing=before.pricing,
    )
    expected_before = manual_total(
        unit_price=unit_price,
        quantity=1,
        discount_bps=500,
        tax_bps=0,
        minor_units=minor_units,
    )
    expected_after = manual_total(
        unit_price=unit_price,
        quantity=1,
        discount_bps=1000,
        tax_bps=0,
        minor_units=minor_units,
    )
    assert (
        before.pricing.subtotal,
        before.pricing.discount_amount,
        before.pricing.net_amount,
        before.pricing.total,
    ) == expected_before
    assert (
        after.pricing.subtotal,
        after.pricing.discount_amount,
        after.pricing.net_amount,
        after.pricing.total,
    ) == expected_after
    assert after.comparison is not None
    assert after.comparison.total_delta == format(
        Decimal(expected_after[-1]) - Decimal(expected_before[-1]),
        f".{minor_units}f",
    )
    assert after.reason_refs == ("changeset:discount@r1",)
    assert "NOT_A_POLICY_APPROVAL" in after.limitations


def test_dual_formation_has_two_independent_objects_and_evidence(tmp_path: Path) -> None:
    service, _, profile = make_dual_service(tmp_path)
    try:
        service.form_quote()
        view = service.deliverable_set_view()
        assert service.store.get_workspace("default")["profile_digest"] == profile.digest
        assert {item["deliverable_kind"] for item in view["members"]} == {
            "QUOTE",
            "DISCOUNT_MEMO",
        }
        snapshot = service.current_snapshot()
        manifests = [ref for ref in snapshot.manifest_refs if ref.startswith("runtime-dependency:")]
        assert len([ref for ref in manifests if "quote-blue-harbor" in ref]) == 2
        runtime = [service.store.load_artifact(ref).payload for ref in manifests]
        assert len({item["trace_ref"] for item in runtime}) == len(runtime)
        memo = next(item for item in service.current_deliverables() if item.payload["deliverable_kind"] == "DISCOUNT_MEMO")
        assert memo.payload["pricing"]["discount_rate_bps"] == 500
        assert memo.payload["last_price_change_set_ref"] is None
    finally:
        service.close()


def test_tampered_memo_formation_is_zero_write(tmp_path: Path) -> None:
    service, _, _ = make_dual_service(tmp_path)
    try:
        prepared = service.deliverable_formation.prepare(service.profile.task_request())
        body = prepared.model_dump(mode="json", exclude={"digest"})
        memo = next(
            item for item in body["deliverables"] if item["payload"]["deliverable_kind"] == "DISCOUNT_MEMO"
        )
        memo["payload"]["pricing"]["total"] = "0.01"
        memo["digest"] = ""
        body["digest"] = ""
        forged = type(prepared).model_validate(body)
        before = service.store.count_records()
        with pytest.raises(
            IntegrityError,
            match=r"DELIVERABLE_FORMATION_MEMBER_SET_MISMATCH|DELIVERABLE_MEMO_REPLAY_MISMATCH",
        ):
            service.deliverable_formation.commit(forged)
        assert service.store.count_records() == before
        with pytest.raises(KeyError):
            service.current_quote()
    finally:
        service.close()


@pytest.mark.parametrize("mutation", ("returned-ref", "event", "artifact", "idempotency"))
def test_formation_receipt_event_artifacts_and_idempotency_are_replayed(
    tmp_path: Path,
    mutation: str,
) -> None:
    service, _, _ = make_dual_service(tmp_path, name=f"formation-{mutation}")
    try:
        prepared = service.deliverable_formation.prepare(service.profile.task_request())
        body = prepared.model_dump(mode="json", exclude={"digest"})
        if mutation == "returned-ref":
            body["formation_receipt"]["deliverable_refs"][0] = "work:forged@v999"
            body["formation_receipt"]["digest"] = ""
        elif mutation == "event":
            body["event_payload"]["deliverable_refs"][0] = "work:forged@v999"
        elif mutation == "artifact":
            write = next(
                item
                for item in body["artifact_writes"]
                if item["artifact_id"] == prepared.formation_receipt.id
            )
            write["payload"]["deliverable_refs"][0] = "work:forged@v999"
            write["payload"]["digest"] = ""
            write["payload_digest"] = sha256_digest(write["payload"])
        else:
            body["idempotency_key"] = "workspace:form:forged@v999"
        body["digest"] = ""
        forged = type(prepared).model_validate(body)
        before = service.store.count_records()
        with pytest.raises(IntegrityError, match="DELIVERABLE_FORMATION_"):
            service.deliverable_formation.commit(forged)
        assert service.store.count_records() == before
        with pytest.raises(KeyError):
            service.current_quote()
    finally:
        service.close()


def test_second_deliverable_formation_write_failure_rolls_back(tmp_path: Path) -> None:
    service, _, _ = make_dual_service(tmp_path, name="formation-second-write")
    try:
        prepared = service.deliverable_formation.prepare(service.profile.task_request())
        service.deliverable_formation.fail_after = f"deliverable:{service.quote_object_id}"
        before = service.store.count_records()
        with pytest.raises(RuntimeError, match="INJECTED_DELIVERABLE_FORMATION_FAILURE"):
            service.deliverable_formation.commit(prepared)
        assert service.store.count_records() == before
        for deliverable in prepared.deliverables:
            with pytest.raises(KeyError):
                service.store.get_object(deliverable.id)
        service.deliverable_formation.fail_after = None
        receipt = service.deliverable_formation.commit(prepared)
        assert receipt.status == "COMPLETED"
    finally:
        service.close()


@pytest.mark.parametrize("mutation", ("review", "trace", "manifest"))
def test_candidate_review_and_evidence_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
) -> None:
    service, _, _ = make_dual_service(tmp_path, name=f"candidate-{mutation}")
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        candidate = service._deliverable_candidate_record(bundle)
        assert candidate is not None
        assert {item.evidence_mode for item in candidate.members} == {"CANDIDATE"}
        target, other = candidate.members
        original_read = service.store.load_artifact
        selected_ref = {
            "review": target.review_projection_ref,
            "trace": target.trace_ref,
            "manifest": target.manifest_ref,
        }[mutation]
        replacement_ref = {
            "review": other.review_projection_ref,
            "trace": other.trace_ref,
            "manifest": other.manifest_ref,
        }[mutation]
        replacement = original_read(replacement_ref)

        def read(artifact_id, expected_media_type=None):
            if artifact_id == selected_ref:
                return SimpleNamespace(
                    payload=replacement.payload,
                    payload_digest=replacement.payload_digest,
                )
            return original_read(artifact_id, expected_media_type)

        monkeypatch.setattr(service.store, "load_artifact", read)
        with pytest.raises((IntegrityError, ValueError), match=r"CANDIDATE_EVIDENCE|content digest"):
            service._verify_deliverable_candidate_evidence(bundle, candidate)
    finally:
        service.close()


def test_non_price_preserve_keeps_memo_evidence_then_discount_rebuilds(tmp_path: Path) -> None:
    service, _, profile = make_dual_service(tmp_path)
    try:
        service.form_quote()
        memo_id = next(item.object_id for item in profile.members if item.deliverable_kind == "DISCOUNT_MEMO")
        memo_before = service.store.get_object(memo_id)
        initial_snapshot = service.current_snapshot()
        initial_manifest = next(ref for ref in initial_snapshot.manifest_refs if "discount-memo" in ref)
        initial_trace = service.store.load_artifact(initial_manifest).payload["trace_ref"]

        date = propose(service, "date-only", "launch_date", "2026-11-01")
        date_candidates = service._deliverable_candidate_record(date)
        assert date_candidates is not None
        preserved_candidate = next(
            item for item in date_candidates.members if item.object_id == memo_id
        )
        assert preserved_candidate.evidence_mode == "PREDECESSOR_PRESERVED"
        assert preserved_candidate.candidate_ref == memo_before.ref
        preserved_review = service.store.load_artifact(
            preserved_candidate.review_projection_ref
        ).payload
        assert preserved_review["candidate_ref"] == memo_before.ref
        assert {
            item.target_id: item.disposition.value
            for item in date.minimal_rebase_certificate.effects
            if item.target_id in {memo_id, service.quote_object_id}
        } == {
            memo_id: "PRESERVE_WITHIN_BOUNDARY",
            service.quote_object_id: "REBUILD",
        }
        date_result = approve_and_apply(service, "date-only", date)
        memo_preserved = service.store.get_object(memo_id)
        assert memo_preserved == memo_before
        assert initial_manifest in service.current_snapshot().manifest_refs
        assert service.store.load_artifact(initial_trace).payload_digest == service.store.load_artifact(
            initial_trace
        ).payload_digest
        batch_ref = date_result["outcome"]["workspace_rebase_receipt"][
            "deliverable_set_receipt_ref"
        ]
        batch = service.store.load_artifact(batch_ref).payload
        preserved = next(item for item in batch["members"] if item["object_id"] == memo_id)
        assert preserved["disposition"] == "PRESERVE_WITHIN_BOUNDARY"
        assert preserved["result_ref"] == memo_before.ref

        discount = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        assert {
            item.target_id: item.disposition.value
            for item in discount.minimal_rebase_certificate.effects
            if item.target_id in {memo_id, service.quote_object_id}
        } == {memo_id: "REBUILD", service.quote_object_id: "REBUILD"}
        approve_and_apply(service, "discount-10", discount)
        memo_after = service.store.get_object(memo_id)
        assert memo_after.version == "v2"
        assert memo_after.payload["previous_pricing"] == memo_before.payload["pricing"]
        assert memo_after.payload["comparison"]["before_discount_bps"] == 500
        assert memo_after.payload["comparison"]["after_discount_bps"] == 1000
        assert memo_after.payload["last_price_change_set_ref"] == (
            "changeset:workspace-discount-10@r1"
        )
    finally:
        service.close()


def test_partial_then_complete_output_approvals_and_wrong_actor(tmp_path: Path) -> None:
    service, _, profile = make_dual_service(tmp_path)
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        owners = [item.owner_id for item in profile.members]
        with service._test_as_actor(service.change_owner["discount-10"]):
            source = service.approve_change(
                "discount-10",
                actor_id=service.change_owner["discount-10"],
                preview_digest=bundle.preview.digest,
            )
        with service._test_as_actor(owners[0]):
            service.approve_deliverable_set_change(
                "discount-10",
                owner_id=owners[0],
                actor_id=None,
                preview_digest=bundle.preview.digest,
            )
        before = tuple(item.digest for item in service.current_deliverables())
        with pytest.raises(RuntimeError, match="DELIVERABLE_APPROVAL_SET_REQUIRED"):
            service.apply_approved_change(
                "discount-10",
                approval_digest=source["approval_digest"],
            )
        assert tuple(item.digest for item in service.current_deliverables()) == before
        with service._test_as_actor(owners[0]), pytest.raises(
            AuthorizationError,
            match="DELIVERABLE_APPROVER_IDENTITY_MISMATCH",
        ):
            service.approve_deliverable_set_change(
                "discount-10",
                owner_id=owners[1],
                actor_id=owners[1],
                preview_digest=bundle.preview.digest,
            )
        with service._test_as_actor(owners[1]):
            complete = service.approve_deliverable_set_change(
                "discount-10",
                owner_id=owners[1],
                actor_id=None,
                preview_digest=bundle.preview.digest,
            )
        assert complete["approval_set"]["status"] == "COMPLETE"
        assert complete["decision_count"] == 2
        service.apply_approved_change(
            "discount-10",
            approval_digest=source["approval_digest"],
        )
    finally:
        service.close()


def test_output_approval_retry_keeps_first_system_time_and_decision(tmp_path: Path) -> None:
    service, _, profile = make_dual_service(tmp_path, name="approval-retry")
    try:
        service.form_quote()
        service.clock = SystemClock()
        bundle = propose(
            service,
            "discount-retry",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        owner = profile.members[0].owner_id
        with service._test_as_actor(service.change_owner["discount-retry"]):
            service.approve_change(
                "discount-retry",
                actor_id=service.change_owner["discount-retry"],
                preview_digest=bundle.preview.digest,
            )
        with service._test_as_actor(owner):
            first = service.approve_deliverable_set_change(
                "discount-retry",
                owner_id=owner,
                actor_id=None,
                preview_digest=bundle.preview.digest,
                operation_id="decision-retry-001",
            )
        time.sleep(0.002)
        with service._test_as_actor(owner):
            second = service.approve_deliverable_set_change(
                "discount-retry",
                owner_id=owner,
                actor_id=None,
                preview_digest=bundle.preview.digest,
                operation_id="decision-retry-001",
            )
        assert second["decision_digest"] == first["decision_digest"]
        assert second["approval_set"]["decisions"] == first["approval_set"]["decisions"]
        events = service.changes.journal("WORKSPACE_DELIVERABLE_SET_APPROVED")
        assert len(events) == 1
    finally:
        service.close()


def test_legacy_output_decision_and_approval_set_keep_original_digest_shape() -> None:
    decision_body = {
        "owner_id": "human:finance-owner",
        "actor_id": "human:finance-owner",
        "identity_issuer": "https://identity.example",
        "identity_subject": "subject:finance",
        "identity_mode": "VERIFIED_REQUEST_PRINCIPAL",
        "candidate_set_digest": "sha256:" + "a" * 64,
        "authority_revision": "sha256:" + "b" * 64,
        "scopes": ["discount_memo.pricing"],
        "decision": "APPROVED",
        "approved_at": "2026-09-26T00:00:00Z",
        "expires_at": "2026-09-26T00:15:00Z",
        "method": "EXPLICIT_DELIVERABLE_OWNER_COMMAND",
    }
    decision_wire = {**decision_body, "digest": sha256_digest(decision_body)}
    decision = DeliverableApprovalDecision.model_validate(decision_wire)
    assert decision.model_dump(mode="json") == decision_wire

    set_body = {
        "schema_version": "orgrebase.deliverable-approval-set.v1",
        "id": "deliverable-approval-set:legacy@r1",
        "candidate_set_digest": decision_body["candidate_set_digest"],
        "decisions": [decision_wire],
        "status": "INCOMPLETE",
    }
    set_wire = {**set_body, "digest": sha256_digest(set_body)}
    approval_set = DeliverableApprovalSet.model_validate(set_wire)
    assert approval_set.model_dump(mode="json") == set_wire


def test_source_rejection_invalidates_output_actions_and_new_decisions(
    tmp_path: Path,
) -> None:
    service, _, profile = make_dual_service(tmp_path, name="source-rejected")
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-source-rejected",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        source_owner = service.change_owner["discount-source-rejected"]
        with service._test_as_actor(source_owner):
            service.approve_change(
                "discount-source-rejected",
                actor_id=source_owner,
                preview_digest=bundle.preview.digest,
            )
            service.reject_change(
                "discount-source-rejected",
                actor_id=source_owner,
                reason="Source owner withdrew the change",
            )
        output_owner = profile.members[0].owner_id
        with service._test_as_actor(output_owner):
            view = service.deliverable_set_change_view(
                "discount-source-rejected"
            )
            assert view["allowed_actions"] == []
            with pytest.raises(
                FreshnessError,
                match="DELIVERABLE_SOURCE_APPROVAL_NOT_CURRENT",
            ):
                service.approve_deliverable_set_change(
                    "discount-source-rejected",
                    owner_id=output_owner,
                    actor_id=None,
                    preview_digest=bundle.preview.digest,
                    operation_id="rejected-source-output",
                )
        assert service._deliverable_approval_record(bundle) is None
    finally:
        service.close()


def test_two_sqlite_instances_atomically_aggregate_owner_decisions(tmp_path: Path) -> None:
    first, runtime, profile = make_dual_service(tmp_path, name="approval-race")
    second = None
    try:
        first.form_quote()
        bundle = propose(
            first,
            "discount-race",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        with first._test_as_actor(first.change_owner["discount-race"]):
            first.approve_change(
                "discount-race",
                actor_id=first.change_owner["discount-race"],
                preview_digest=bundle.preview.digest,
            )
        second = WorkspaceService.reopen(
            Path(first.store_path),
            runtime_configuration=runtime,
            deliverable_set_profile=profile,
        )
        _, second_actor = identity_directory(second)
        owners = tuple(item.owner_id for item in profile.members)
        barrier = Barrier(2)
        original_transactions = (first.store.transaction, second.store.transaction)
        for service in (first, second):
            original = service.store.transaction

            @contextmanager
            def synchronized_transaction(_original=original):
                barrier.wait(timeout=5)
                with _original() as connection:
                    yield connection

            service.store.transaction = synchronized_transaction

        def decide(service, as_actor, owner, operation):
            with as_actor(owner):
                return service.approve_deliverable_set_change(
                    "discount-race",
                    owner_id=owner,
                    actor_id=None,
                    preview_digest=bundle.preview.digest,
                    operation_id=operation,
                )["approval_set"]["status"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                pool.map(
                    lambda args: decide(*args),
                    (
                        (first, first._test_as_actor, owners[0], "race-owner-1"),
                        (second, second_actor, owners[1], "race-owner-2"),
                    ),
                )
            )
        first.store.transaction, second.store.transaction = original_transactions
        assert sorted(results) == ["COMPLETE", "INCOMPLETE"]
        approval_set = first._deliverable_approval_record(bundle)
        assert approval_set is not None and approval_set.status == "COMPLETE"
        assert {item.owner_id for item in approval_set.decisions} == set(owners)
    finally:
        if second is not None:
            second.close()
        first.close()


def test_deliverable_view_uses_one_database_snapshot_during_concurrent_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, runtime, profile = make_dual_service(tmp_path, name="snapshot-read")
    writer = None
    try:
        reader.form_quote()
        bundle = propose(
            reader,
            "discount-snapshot",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        source, _ = approve_all(reader, "discount-snapshot", bundle)
        writer = WorkspaceService.reopen(
            Path(reader.store_path),
            runtime_configuration=runtime,
            deliverable_set_profile=profile,
        )
        _, writer_actor = identity_directory(writer)
        member_ids = {item.object_id for item in profile.members}
        observed_first, release_reader = Event(), Event()
        original_get = reader.store.get_object
        seen = {"count": 0}

        def paused_get(object_id, version=None):
            value = original_get(object_id, version)
            if version is None and object_id in member_ids:
                seen["count"] += 1
                if seen["count"] == 1:
                    observed_first.set()
                    assert release_reader.wait(timeout=5)
            return value

        monkeypatch.setattr(reader.store, "get_object", paused_get)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(reader.deliverable_set_view)
            assert observed_first.wait(timeout=5)
            with writer_actor("executor:one"):
                writer.apply_approved_change(
                    "discount-snapshot",
                    approval_digest=source["approval_digest"],
                )
            release_reader.set()
            during = pending.result(timeout=5)
        assert {item["object_ref"].rsplit("@", 1)[1] for item in during["members"]} == {
            "v1"
        }
        monkeypatch.setattr(reader.store, "get_object", original_get)
        assert {
            item["object_ref"].rsplit("@", 1)[1]
            for item in reader.deliverable_set_view()["members"]
        } == {"v2"}
    finally:
        if "release_reader" in locals():
            release_reader.set()
        if writer is not None:
            writer.close()
        reader.close()


def test_verified_finance_principal_cannot_approve_quote_owner_scope(tmp_path: Path) -> None:
    service, _, profile = make_dual_service(tmp_path, name="principal-split")
    try:
        service.form_quote()
        members, as_actor = identity_directory(service)
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        finance_owner = next(
            item.owner_id for item in profile.members if item.deliverable_kind == "DISCOUNT_MEMO"
        )
        quote_owner = next(
            item.owner_id for item in profile.members if item.deliverable_kind == "QUOTE"
        )
        with as_actor(finance_owner):
            source = service.approve_change(
                "discount-10",
                actor_id=finance_owner,
                preview_digest=bundle.preview.digest,
            )
            first = service.approve_deliverable_set_change(
                "discount-10",
                owner_id=finance_owner,
                actor_id=None,
                preview_digest=bundle.preview.digest,
            )
            assert first["approval_set"]["status"] == "INCOMPLETE"
            with pytest.raises(
                AuthorizationError,
                match="DELIVERABLE_APPROVER_IDENTITY_MISMATCH",
            ):
                service.approve_deliverable_set_change(
                    "discount-10",
                    owner_id=quote_owner,
                    actor_id=quote_owner,
                    preview_digest=bundle.preview.digest,
                )
        assert service._deliverable_approval_record(bundle) is None
        with as_actor(quote_owner):
            second = service.approve_deliverable_set_change(
                "discount-10",
                owner_id=quote_owner,
                actor_id=None,
                preview_digest=bundle.preview.digest,
            )
        assert second["approval_set"]["status"] == "COMPLETE"
        with as_actor("executor:one"):
            service.apply_approved_change(
                "discount-10",
                approval_digest=source["approval_digest"],
            )
        assert set(members) >= {finance_owner, quote_owner}
    finally:
        service.close()


def test_body_actor_source_approval_stays_compatible_but_cannot_sign_outputs(
    tmp_path: Path,
) -> None:
    service, _, profile = make_dual_service(tmp_path, name="body-boundary")
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        source = service.approve_change(
            "discount-10",
            actor_id=service.change_owner["discount-10"],
            preview_digest=bundle.preview.digest,
        )
        assert source["approval"]["actor_id"] == service.change_owner["discount-10"]
        owner = profile.members[0].owner_id
        with pytest.raises(
            AuthenticationError,
            match="AUTH_VERIFIED_PRINCIPAL_REQUIRED",
        ):
            service.approve_deliverable_set_change(
                "discount-10",
                owner_id=owner,
                actor_id=owner,
                preview_digest=bundle.preview.digest,
            )
        assert service._deliverable_approval_record(bundle) is None
    finally:
        service.close()


def test_output_membership_revocation_and_responsibility_drift_block_apply(
    tmp_path: Path,
) -> None:
    service, _, profile = make_dual_service(tmp_path, name="principal-revoke")
    try:
        service.form_quote()
        members, as_actor = identity_directory(service)
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        finance_owner = next(
            item.owner_id for item in profile.members if item.deliverable_kind == "DISCOUNT_MEMO"
        )
        quote_owner = next(
            item.owner_id for item in profile.members if item.deliverable_kind == "QUOTE"
        )
        with as_actor(finance_owner):
            source = service.approve_change(
                "discount-10",
                actor_id=finance_owner,
                preview_digest=bundle.preview.digest,
            )
            service.approve_deliverable_set_change(
                "discount-10",
                owner_id=finance_owner,
                actor_id=None,
                preview_digest=bundle.preview.digest,
            )
        with as_actor(quote_owner):
            service.approve_deliverable_set_change(
                "discount-10",
                owner_id=quote_owner,
                actor_id=None,
                preview_digest=bundle.preview.digest,
            )
        before = tuple(item.digest for item in service.current_deliverables())
        removed = members.pop(quote_owner)
        with as_actor("executor:one"), pytest.raises(
            AuthenticationError,
            match="AUTH_MEMBERSHIP_DENIED",
        ):
            service.apply_approved_change(
                "discount-10",
                approval_digest=source["approval_digest"],
            )
        assert tuple(item.digest for item in service.current_deliverables()) == before
        members[quote_owner] = removed

        current = service.enterprise_binding
        resources = tuple(
            item.model_copy(update={"owner_id": "human:new-finance-owner"})
            if item.slot_id == "pricing_policy"
            else item
            for item in current.resources
        )
        migrated = EnterpriseBinding.model_validate(
            {
                **current.model_dump(mode="json", exclude={"digest"}),
                "resources": [item.model_dump(mode="json") for item in resources],
            }
        )
        with service.store.transaction() as connection:
            activate_binding(
                service,
                connection,
                binding=migrated,
                expected_revision=binding_revision(service),
                slot_id="pricing_policy",
                migration_digest="sha256:" + "a" * 64,
            )
        candidate = service._deliverable_candidate_record(bundle)
        approval_set = service._deliverable_approval_record(bundle)
        assert candidate is not None and approval_set is not None
        with pytest.raises(
            (FreshnessError, IntegrityError),
            match=r"DELIVERABLE_OWNER_RESPONSIBILITY_CHANGED|DELIVERABLE_APPROVAL_AUTHORITY_INVALID",
        ):
            service._verify_deliverable_approval_set(bundle, candidate, approval_set)
        assert tuple(item.digest for item in service.current_deliverables()) == before
    finally:
        service.close()


def test_rejected_or_unknown_member_blocks_entire_batch(tmp_path: Path, monkeypatch) -> None:
    rejected, _, _ = make_dual_service(tmp_path, name="rejected")
    try:
        rejected.form_quote()
        bundle = propose(
            rejected,
            "discount-rejected",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        owners = required_owners(rejected, bundle)
        rejected_owner = next(iter(owners))
        with rejected._test_as_actor(rejected.change_owner["discount-rejected"]):
            source = rejected.approve_change(
                "discount-rejected",
                actor_id=rejected.change_owner["discount-rejected"],
                preview_digest=bundle.preview.digest,
            )
        with rejected._test_as_actor(rejected_owner):
            rejected.approve_deliverable_set_change(
                "discount-rejected",
                owner_id=rejected_owner,
                actor_id=None,
                decision="REJECTED",
                preview_digest=bundle.preview.digest,
            )
        before = tuple(item.digest for item in rejected.current_deliverables())
        with pytest.raises(IntegrityError, match="DELIVERABLE_APPROVAL_SET_INCOMPLETE"):
            rejected.apply_approved_change(
                "discount-rejected",
                approval_digest=source["approval_digest"],
            )
        assert tuple(item.digest for item in rejected.current_deliverables()) == before
    finally:
        rejected.close()

    unknown, _, _ = make_dual_service(tmp_path, name="unknown")
    try:
        unknown.form_quote()
        bundle = propose(
            unknown,
            "discount-unknown",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        original = unknown._build_deliverable_candidate_set(bundle)
        payload = original.candidate_set.model_dump(mode="json", exclude={"digest"})
        payload["members"][0]["disposition"] = "HOLD_FOR_REVIEW"
        payload["members"][0]["evidence_mode"] = "PREDECESSOR_UNRESOLVED"
        payload["members"][0]["required_scopes"] = []
        payload["members"][0]["digest"] = ""
        payload["state"] = "UNKNOWN"
        candidate = DeliverableCandidateSet.model_validate(payload)
        monkeypatch.setattr(unknown, "_deliverable_candidate_record", lambda value: candidate)
        monkeypatch.setattr(
            unknown,
            "_verify_deliverable_candidate_evidence",
            lambda *args: None,
        )
        before = tuple(item.digest for item in unknown.current_deliverables())
        with pytest.raises(RuntimeError, match="DELIVERABLE_SET_UNKNOWN_BLOCKS_APPROVAL"):
            unknown.approve_deliverable_set_change(
                "discount-unknown",
                owner_id=next(iter(required_owners(unknown, bundle))),
                actor_id=next(iter(required_owners(unknown, bundle))),
                preview_digest=bundle.preview.digest,
            )
        assert tuple(item.digest for item in unknown.current_deliverables()) == before
    finally:
        unknown.close()


@pytest.mark.parametrize("mutation", ("expired", "authority-drift"))
def test_stale_output_approval_is_zero_write(tmp_path: Path, monkeypatch, mutation: str) -> None:
    service, _, _ = make_dual_service(tmp_path, name=mutation)
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        source, _ = approve_all(service, "discount-10", bundle)
        approval_set = service._deliverable_approval_record(bundle)
        assert approval_set is not None
        body = approval_set.model_dump(mode="json", exclude={"digest"})
        for decision in body["decisions"]:
            if mutation == "expired":
                decision["expires_at"] = decision["approved_at"]
            else:
                decision["authority_revision"] = "authz:drifted@r2"
            decision["digest"] = ""
        body["digest"] = ""
        stale = type(approval_set).model_validate(body)
        monkeypatch.setattr(service, "_deliverable_approval_record", lambda value: stale)
        before = tuple(item.digest for item in service.current_deliverables())
        with pytest.raises(IntegrityError, match="DELIVERABLE_APPROVAL_AUTHORITY_INVALID"):
            service.apply_approved_change(
                "discount-10",
                approval_digest=source["approval_digest"],
            )
        assert tuple(item.digest for item in service.current_deliverables()) == before
    finally:
        service.close()


def test_second_member_evidence_failure_rolls_back_and_retry_is_idempotent(tmp_path: Path) -> None:
    service, runtime, profile = make_dual_service(tmp_path)
    path = Path(service.store_path)
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        source, _ = approve_all(service, "discount-10", bundle)
        approval = Approval.model_validate(source["approval"])
        before = (
            tuple(item.digest for item in service.current_deliverables()),
            service.current_graph_pointer().digest,
            service.store.get_object("policy:finance.pricing").digest,
            service.store.verify_event_chain()["head_digest"],
        )
        memo_manifest = next(
            member.manifest_ref for member in service._deliverable_candidate_record(bundle).members
            if member.deliverable_kind == "DISCOUNT_MEMO"
        )
        with pytest.raises(RuntimeError, match="INJECTED_WORKSPACE_SUCCESSOR_FAILURE"):
            service._execute_apply(
                kind="discount-10",
                bundle=bundle,
                approval=approval,
                fail_after=f"artifact:{memo_manifest}",
            )
        after = (
            tuple(item.digest for item in service.current_deliverables()),
            service.current_graph_pointer().digest,
            service.store.get_object("policy:finance.pricing").digest,
            service.store.verify_event_chain()["head_digest"],
        )
        assert after == before
        first = service.apply_approved_change("discount-10", approval_digest=approval.digest)
        second = service.apply_approved_change("discount-10", approval_digest=approval.digest)
        assert first["artifact_digest"] == second["artifact_digest"]
    finally:
        service.close()
    reopened = WorkspaceService.reopen(
        path,
        runtime_configuration=runtime,
        deliverable_set_profile=profile,
    )
    try:
        assert {item.version for item in reopened.current_deliverables()} == {"v2"}
        assert reopened.store.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()


def test_overlay_is_new_workspace_only_and_runtime_bound(tmp_path: Path) -> None:
    service, runtime, profile = make_dual_service(tmp_path, name="overlay")
    path = Path(service.store_path)
    try:
        service.form_quote()
        revision = workspace_revision(service)
        assert revision["deliverable_set_profile"] == {
            "profile_ref": profile.ref,
            "profile_digest": profile.digest,
            "runtime_revision": profile.runtime_revision,
        }
    finally:
        service.close()
    with pytest.raises(RuntimeError, match="WORKSPACE_DELIVERABLE_SET_PROFILE_REQUIRED"):
        WorkspaceService.reopen(path, runtime_configuration=runtime)

    changed_body = profile.model_dump(mode="json", exclude={"digest"})
    changed_body["runtime_revision"] = "runtime:quote-discount-memo@v2"
    changed = type(profile).model_validate(changed_body)
    with pytest.raises(
        (IntegrityError, RuntimeError),
        match=r"WORKSPACE_BINDING_MISMATCH|DELIVERABLE_SET",
    ):
        WorkspaceService.reopen(
            path,
            runtime_configuration=runtime,
            deliverable_set_profile=changed,
        )

    old_path = tmp_path / "old.sqlite"
    old = WorkspaceService(store_path=old_path, runtime_configuration=runtime)
    try:
        old.form_quote()
        old_revision = workspace_revision(old)
        assert "deliverable_set_profile" not in old_revision
        old_quote = deepcopy(old.current_quote().model_dump(mode="json"))
    finally:
        old.close()
    with pytest.raises(RuntimeError, match="WORKSPACE_DELIVERABLE_SET_BINDING_MISSING"):
        WorkspaceService.reopen(
            old_path,
            runtime_configuration=runtime,
            deliverable_set_profile=profile,
        )
    old = WorkspaceService.reopen(old_path, runtime_configuration=runtime)
    try:
        assert old.current_quote().model_dump(mode="json") == old_quote
    finally:
        old.close()


def test_missing_memo_handler_is_zero_write(tmp_path: Path, monkeypatch) -> None:
    service, _, _ = make_dual_service(tmp_path)
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        source, _ = approve_all(service, "discount-10", bundle)
        approval = Approval.model_validate(source["approval"])
        original = service._create_rebase_workflow

        def without_memo(*args, **kwargs):
            workflow = original(*args, **kwargs)
            workflow.rebuild_handlers.pop("DISCOUNT_MEMO")
            return workflow

        monkeypatch.setattr(service, "_create_rebase_workflow", without_memo)
        before = tuple(item.digest for item in service.current_deliverables())
        with pytest.raises(IntegrityError, match="NO_REBUILD_HANDLER"):
            service._execute_apply(kind="discount-10", bundle=bundle, approval=approval)
        assert tuple(item.digest for item in service.current_deliverables()) == before
    finally:
        service.close()
