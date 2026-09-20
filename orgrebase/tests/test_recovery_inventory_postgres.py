from __future__ import annotations

import json
from pathlib import Path

import httpx2 as httpx
import psycopg
import pytest
from psycopg import sql
from test_recovery_inventory import CREATED, SINCE, UNTIL, ReceiptServer, effect, receipt

from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.domain import IntegrityError
from orgrebase.recovery_inventory import recovery_inventory
from orgrebase.store import StateStore
from orgrebase.store_operations import backup_postgres, database_dsn, export_deletion_ledger, restore_postgres


def test_real_postgres_restore_inventory_finds_post_backup_effect_and_keeps_database_quarantined(postgres_dsn, tmp_path: Path):
    tenant = "tenant:restore-inventory"
    server = ReceiptServer()
    settings = DataverseDraftTargetSettings(tenant_id=tenant, instance_url="https://sales.example",
                                            quote_id="00000000-0000-0000-0000-000000000123")
    target = DataverseDraftTarget(settings, lambda: "read-only", transport=httpx.MockTransport(server))
    backup = tmp_path / "backup"
    with StateStore(postgres_dsn, tenant_id=tenant) as source:
        source.record_event("PRE_BACKUP", {})
        backup_postgres(postgres_dsn, tenant_id=tenant, output=backup)
        request = effect(target)
        with source.transaction() as connection:
            source.put_effect(connection, effect_id=request.effect_id, target_key=request.barrier_key,
                              request_digest=request.digest, request=request.model_dump(mode="json"), created_at=CREATED)
            source.update_effect(connection, effect_id=request.effect_id, expected_state="READY", state="CONFIRMED",
                                 updated_at=CREATED, result={"outcome": "CONFIRMED"})
        receipt(server, target, request)
        deletion_ledger = export_deletion_ledger(postgres_dsn, tenant_id=tenant)
        restored = restore_postgres(postgres_dsn, tenant_id=tenant, backup=backup,
                                    latest_deletion_ledger=deletion_ledger)
        name = restored["database_name"]
    restored_dsn = database_dsn(postgres_dsn, name)
    try:
        with StateStore(restored_dsn, tenant_id=tenant, maintenance=True, read_only=True) as store:
            before = store.count_records()
            report = recovery_inventory(store, target, since=SINCE, until=UNTIL)
            assert report["status"] == "RECONCILIATION_REQUIRED"
            assert report["findings"][0]["effect_id"] == request.effect_id
            assert report["findings"][0]["classification"] == "MISSING_FROM_RESTORED_DATABASE"
            assert store.count_records() == before and store.get_effect(request.effect_id) is None
            assert store.check_health()["recovery_required"] and not report["writes_released"]
            assert set(item.method for item in server.requests) == {"GET"}
            assert json.loads(json.dumps(report))["production_ready"] is False
        with pytest.raises(IntegrityError, match="RECOVERY_QUALIFICATION_REQUIRED"):
            StateStore(restored_dsn, tenant_id=tenant, migrate=False)
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
