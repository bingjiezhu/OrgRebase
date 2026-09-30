from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from orgrebase.workspace.formation import quote_discount_memo_profile
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_deliverable_set import (
    approve_and_apply,
    identity_directory,
    propose,
)
from tests.workspace.test_priced_quote_pack import POLICY, priced_draft


def test_postgres_dual_formation_apply_and_restart(postgres_runtime, tmp_path) -> None:
    seal_enterprise_quote_pilot_pack(priced_draft(tmp_path), tmp_path / "pack")
    runtime = load_enterprise_quote_pilot_pack(tmp_path / "pack")
    profile = quote_discount_memo_profile(runtime)
    credentials = postgres_runtime(tenant_id=runtime.profile.organization_id)
    kwargs = {
        "runtime_configuration": runtime,
        "deliverable_set_profile": profile,
        "store_path": credentials["runtime_dsn"],
        "store_tenant_id": runtime.profile.organization_id,
        "store_migrate": False,
        "review_duration_seconds": 0,
    }
    first = WorkspaceService(**kwargs)
    second = None
    try:
        first.form_quote()
        memo_id = next(
            item.object_id
            for item in profile.members
            if item.deliverable_kind == "DISCOUNT_MEMO"
        )
        bundle = propose(
            first,
            "discount-10",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        approve_and_apply(first, "discount-10", bundle)
        assert first.current_quote().version == "v2"
        assert first.store.get_object(memo_id).version == "v2"
        assert first.store.runtime_role_safe() is True
        second = WorkspaceService(**kwargs)
        assert second.current_quote().digest == first.current_quote().digest
        assert second.store.get_object(memo_id).digest == first.store.get_object(memo_id).digest
        assert second.store.verify_event_chain()["status"] == "PASS"
        assert second.deliverable_set_view()["external_effects"] == "DISABLED"
    finally:
        if second is not None:
            second.close()
        first.close()


def test_postgres_two_instances_serialize_output_approval_collection(
    postgres_runtime,
    tmp_path,
) -> None:
    seal_enterprise_quote_pilot_pack(priced_draft(tmp_path), tmp_path / "pack-race")
    runtime = load_enterprise_quote_pilot_pack(tmp_path / "pack-race")
    profile = quote_discount_memo_profile(runtime)
    credentials = postgres_runtime(tenant_id=runtime.profile.organization_id)
    kwargs = {
        "runtime_configuration": runtime,
        "deliverable_set_profile": profile,
        "store_path": credentials["runtime_dsn"],
        "store_tenant_id": runtime.profile.organization_id,
        "store_migrate": False,
        "review_duration_seconds": 0,
    }
    first, second = WorkspaceService(**kwargs), WorkspaceService(**kwargs)
    try:
        _, first_actor = identity_directory(first)
        _, second_actor = identity_directory(second)
        first.form_quote()
        bundle = propose(
            first,
            "discount-race",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000},
        )
        with first_actor(first.change_owner["discount-race"]):
            first.approve_change(
                "discount-race",
                actor_id=first.change_owner["discount-race"],
                preview_digest=bundle.preview.digest,
            )
        owners = tuple(item.owner_id for item in profile.members)
        ready = Barrier(3)

        def decide(service, as_actor, owner, operation):
            ready.wait(timeout=5)
            with as_actor(owner):
                return service.approve_deliverable_set_change(
                    "discount-race",
                    owner_id=owner,
                    actor_id=None,
                    preview_digest=bundle.preview.digest,
                    operation_id=operation,
                )["approval_set"]["status"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(decide, first, first_actor, owners[0], "pg-owner-1"),
                pool.submit(decide, second, second_actor, owners[1], "pg-owner-2"),
            )
            ready.wait(timeout=5)
            results = tuple(future.result(timeout=10) for future in futures)
        assert sorted(results) == ["COMPLETE", "INCOMPLETE"]
        approval_set = first._deliverable_approval_record(bundle)
        assert approval_set is not None and approval_set.status == "COMPLETE"
        assert {item.owner_id for item in approval_set.decisions} == set(owners)
    finally:
        second.close()
        first.close()
