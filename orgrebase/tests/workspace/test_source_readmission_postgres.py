from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.source_readmission import apply_group, approve_group
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_continuous_changes import PACK, apply
from tests.workspace.test_source_readmission_groups import command, prepare


def test_normal_postgres_role_two_connections_commit_one_source_group(postgres_runtime, monkeypatch):
    runtime = load_enterprise_quote_pilot_pack(PACK)
    credentials = postgres_runtime(tenant_id=runtime.profile.organization_id)
    kwargs = {"runtime_configuration": runtime, "store_path": credentials["runtime_dsn"],
              "store_tenant_id": runtime.profile.organization_id, "store_migrate": False,
              "review_duration_seconds": 0}
    first = WorkspaceService(**kwargs)
    second = None
    try:
        first.form_quote()
        for event_id in ("currency", "launch_date"):
            apply(first, event_id)
        detail = prepare(first)
        for owner in detail["owners"]:
            approve_group(first, "recover-1", command(detail, owner["owner_id"]))
        second = WorkspaceService(**kwargs)
        predecessor = first.current_quote()
        barrier = Barrier(2)
        def synchronized_factory(factory):
            def create(*args, **options):
                workflow = factory(*args, **options)
                apply_kernel = workflow.apply
                def synchronized_apply(**request):
                    barrier.wait(timeout=30)
                    return apply_kernel(**request)
                workflow.apply = synchronized_apply
                return workflow
            return create
        for service in (first, second):
            monkeypatch.setattr(service, "_create_rebase_workflow", synchronized_factory(service._create_rebase_workflow))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=60) for future in [
                pool.submit(apply_group, service, "recover-1", command(detail)) for service in (first, second)]]
        assert results[0]["outcome"] == results[1]["outcome"]
        assert first.current_quote().version == f"v{int(predecessor.version[1:]) + 1}"
        assert second.current_quote().digest == first.current_quote().digest
        assert len(first.changes.journal("WORKSPACE_SOURCE_GROUP_APPLIED", subject_key="recover-1")) == 1
        assert first.changes.pending_count == second.changes.pending_count == 0
        assert first.store.verify_event_chain()["status"] == "PASS"
    finally:
        if second is not None:
            second.close()
        first.close()
