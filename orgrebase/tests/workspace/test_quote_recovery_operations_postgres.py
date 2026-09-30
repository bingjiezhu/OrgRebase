from __future__ import annotations

from pathlib import Path

from orgrebase.store import StateStore
from orgrebase.workspace.quote_recovery_operations import (
    QuoteRecoveryNewRun,
    execute_quote_recovery_new_run,
    read_quote_recovery_operation,
)
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_quote_recovery_adoption_matrix import _verified_release
from tests.workspace.test_quote_recovery_governed_learning import (
    RUNTIME,
    TENANT,
    _identity,
    _service,
)
from tests.workspace.test_quote_recovery_operations import (
    _policy,
    _recovery_workspace,
)


def test_postgres_standard_entry_recovers_exact_consumption_after_restart(
    postgres_runtime,
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = postgres_runtime(tenant_id=TENANT)
    with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as first:
        service, _, candidate, _, checks, _ = _verified_release(first)
        workspace, event, _ = _recovery_workspace(first, monkeypatch)
        policy = _policy(tmp_path / "policy.json", candidate=candidate, service=service)
        command = QuoteRecoveryNewRun(
            operation_key="postgres-normal-entry",
            event_id=event.event_id,
            candidate_ref=candidate,
            new_run_id="run:postgres-normal-entry",
        )
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            expected = execute_quote_recovery_new_run(
                workspace,
                command,
                config_path=policy,
            )
        assert expected["status"] == "CONSUMED"

    with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as second:
        resumed = _service(second)
        workspace, _, detail = _recovery_workspace(
            second,
            monkeypatch,
            persist_receipts=False,
        )
        detail["status"] = "REJECTED"
        detail["outcome"] = None
        with _identity(RUNTIME, frozenset({"reader"}), []):
            recovered = execute_quote_recovery_new_run(
                workspace,
                command,
                config_path=policy,
            )
        assert recovered == expected
        assert len(resumed._family("invocation-result")) == 1
        assert second.verify_event_chain()["status"] == "PASS"
        _policy(
            policy,
            candidate=candidate,
            service=resumed,
            enabled=False,
        )
        with _identity(RUNTIME, frozenset({"reader"}), []):
            assert read_quote_recovery_operation(
                workspace,
                command.operation_key,
            ) == expected
