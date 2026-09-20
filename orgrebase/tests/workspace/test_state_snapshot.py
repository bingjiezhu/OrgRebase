from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_continuous_changes import PACK, apply


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_state_does_not_mix_a_quote_with_a_later_commit(backend, tmp_path, request, monkeypatch):
    pack = load_enterprise_quote_pilot_pack(PACK)
    if backend == "postgresql":
        database = request.getfixturevalue("postgres_runtime")(tenant_id=pack.profile.organization_id)
        options = {"store_path": database["runtime_dsn"], "store_tenant_id": pack.profile.organization_id,
                   "store_migrate": False, "runtime_configuration": pack}
    else:
        options = {"store_path": tmp_path / "state.sqlite", "runtime_configuration": pack}
    first = WorkspaceService(**options)
    second = None
    try:
        first.form_quote()
        second = WorkspaceService(**options)
        before = first.state()
        quote_reader = first.current_quote
        committed = False

        def commit_after_quote_read():
            nonlocal committed
            quote = quote_reader()
            if not committed:
                committed = True
                apply(second, "currency")
            return quote

        monkeypatch.setattr(first, "current_quote", commit_after_quote_read)
        during = first.state()
        assert committed
        assert during["quote"] == before["quote"]
        assert during["graph_snapshot"] == before["graph_snapshot"]
        assert during["event_chain"] == before["event_chain"]
        assert during["changes"]["currency"]["outcome"] is None
        after = first.state()
        assert after["quote"]["digest"] == second.current_quote().digest
        assert after["changes"]["currency"]["outcome"] is not None
    finally:
        if second is not None:
            second.close()
        first.close()


def test_reading_a_missing_intake_nonce_does_not_repair_the_database(tmp_path):
    with closing(WorkspaceService(store_path=tmp_path / "nonce.sqlite")) as workspace:
        before = tuple(workspace.store.connection.iterdump())
        with workspace.store.read_snapshot(), pytest.raises(
            IntegrityError, match="WORKSPACE_TASK_INTAKE_INSTANCE_BINDING_MISSING",
        ):
            workspace.task_intake_workspace_instance_nonce(create=False)
        assert tuple(workspace.store.connection.iterdump()) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_concurrent_intake_initialization_returns_one_durable_nonce(backend, tmp_path, request, monkeypatch):
    pack = load_enterprise_quote_pilot_pack(PACK)
    if backend == "postgresql":
        database = request.getfixturevalue("postgres_runtime")(tenant_id=pack.profile.organization_id)
        options = {"store_path": database["runtime_dsn"], "store_tenant_id": pack.profile.organization_id,
                   "store_migrate": False, "runtime_configuration": pack}
    else:
        options = {"store_path": tmp_path / "nonce.sqlite", "runtime_configuration": pack}
    first, second = WorkspaceService(**options), WorkspaceService(**options)
    both_observed_missing = Barrier(2)

    def reader(store):
        original = store.get_idempotent

        def read(key, digest, *, connection=None):
            result = original(key, digest, connection=connection)
            if connection is None and result is None:
                both_observed_missing.wait(timeout=10)
            return result
        return read

    try:
        for workspace in (first, second):
            monkeypatch.setattr(workspace.store, "get_idempotent", reader(workspace.store))
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(workspace.task_intake_workspace_instance_nonce) for workspace in (first, second)]
            nonces = [future.result(timeout=15) for future in pending]
        assert nonces[0] == nonces[1]
        assert first.task_intake_workspace_instance_nonce(create=False) == nonces[0]
        assert second.task_intake_workspace_instance_nonce(create=False) == nonces[0]
    finally:
        first.close()
        second.close()
