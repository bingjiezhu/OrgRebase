from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import httpx2 as httpx
import pytest
from sqlalchemy import update

from orgrebase.commit_gateway import EffectError, EffectRequest
from orgrebase.database import store_metadata
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.recovery_inventory import recovery_inventory
from orgrebase.store import IntegrityError, StateStore

SINCE = "2026-09-09T00:00:00Z"
UNTIL = "2026-09-10T00:00:00Z"
CREATED = "2026-09-09T01:00:00Z"


class ReceiptServer:
    def __init__(self):
        self.receipts = {}
        self.requests = []
        self.cursor_mode = None
        self.page_size = 100

    def __call__(self, request):
        assert request.method == "GET"
        self.requests.append(request)
        if request.url.path.endswith("/orgrebase_effectreceipts"):
            assert request.headers["Prefer"] == "odata.maxpagesize=100"
            params = dict(parse_qsl(request.url.query.decode()))
            index = int(params.get("$skiptoken", "0"))
            records = list(self.receipts.values())
            data = {"value": records[index:index + self.page_size]}
            if index + self.page_size < len(records) or self.cursor_mode:
                params["$skiptoken"] = str(index + self.page_size)
                link = "https://sales.example/api/data/v9.2/orgrebase_effectreceipts?" + urlencode(params)
                if self.cursor_mode == "host":
                    link = link.replace("sales.example", "credentials.attacker.example")
                elif self.cursor_mode == "filter":
                    params["$filter"] = "createdon ne null"
                    link = link.split("?", 1)[0] + "?" + urlencode(params)
                elif self.cursor_mode == "duplicate":
                    link += "&%24skiptoken=another"
                elif self.cursor_mode == "path":
                    link = link.replace("/orgrebase_effectreceipts?", "/quotes?")
                elif self.cursor_mode == "fragment":
                    link += "#unexpected"
                data["@odata.nextLink"] = link
            return httpx.Response(200, json=data)
        identity = request.url.path.rsplit("(", 1)[1][:-1]
        receipt = self.receipts.get(identity)
        return httpx.Response(200, json=receipt) if receipt else httpx.Response(404)


@pytest.fixture
def target():
    settings = DataverseDraftTargetSettings(tenant_id="tenant:inventory", instance_url="https://sales.example",
                                            quote_id="00000000-0000-0000-0000-000000000123")
    server = ReceiptServer()
    adapter = DataverseDraftTarget(settings, lambda: "target-read-only", transport=httpx.MockTransport(server))
    return server, adapter


def effect(target, identity="effect:lost"):
    return EffectRequest(effect_id=identity, tenant_id=target.settings.tenant_id, target_key=target.settings.target_key,
                         action=target.action, expected_version='W/"41"', approval_digest="sha256:" + "a" * 64,
                         payload={"changes": {"description": "Approved metadata"}})


def receipt(server, target, request, outcome="CONFIRMED", created=CREATED):
    record = {**target._receipt(request, outcome), "createdon": created}
    server.receipts[target.receipt_id(request)] = record
    return record


def quarantine(store):
    with store.transaction() as connection:
        store._execute(connection, update(store_metadata).values(recovery_required=1))


def test_target_inventory_pages_preserve_scope_and_deterministic_receipt_identity(target):
    server, adapter = target
    server.page_size = 1
    first = effect(adapter, "effect:first")
    second = effect(adapter, "effect:second")
    receipt(server, adapter, first)
    receipt(server, adapter, second, "REJECTED")
    page = adapter.effect_inventory_page(since=SINCE, until=UNTIL)
    assert page["items"][0]["orgrebase_effectid"] == first.effect_id
    assert page["next_cursor"]
    next_page = adapter.effect_inventory_page(since=SINCE, until=UNTIL, cursor=page["next_cursor"])
    assert next_page["items"][0]["orgrebase_effectid"] == second.effect_id
    assert next_page["next_cursor"] is None


@pytest.mark.parametrize("mode", ["host", "filter", "duplicate", "path", "fragment"])
def test_target_inventory_rejects_response_cursor_before_following_it(target, mode):
    server, adapter = target
    server.cursor_mode = mode
    with pytest.raises(EffectError, match="TARGET_INVENTORY_CURSOR_"):
        adapter.effect_inventory_page(since=SINCE, until=UNTIL)
    assert len(server.requests) == 1


@pytest.mark.parametrize("field,value", [
    ("orgrebase_effectreceiptid", "00000000-0000-0000-0000-000000000999"),
    ("orgrebase_tenantid", "another-tenant"), ("orgrebase_outcome", []),
    ("orgrebase_requestdigest", "sha256:invalid"), ("createdon", UNTIL),
    ("orgrebase_predecessorversion", 'W/"41"\r\nInjected: true'),
])
def test_target_inventory_rejects_malformed_or_unbound_receipts(target, field, value):
    server, adapter = target
    record = receipt(server, adapter, effect(adapter))
    record[field] = value
    with pytest.raises(EffectError, match="TARGET_INVENTORY_"):
        adapter.effect_inventory_page(since=SINCE, until=UNTIL)


def test_recovery_discovers_intent_lost_after_backup_without_any_mutation(target, tmp_path: Path):
    server, adapter = target
    path = tmp_path / "restored.sqlite"
    with StateStore(path, tenant_id=adapter.settings.tenant_id, maintenance=True) as store:
        quarantine(store)
    request = effect(adapter)
    receipt(server, adapter, request)
    before = path.read_bytes()
    with StateStore(path, tenant_id=adapter.settings.tenant_id, maintenance=True, read_only=True) as store:
        report = recovery_inventory(store, adapter, since=SINCE, until=UNTIL)
        assert store.get_effect(request.effect_id) is None
        assert store.check_health()["recovery_required"]
    assert path.read_bytes() == before
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert report["findings"][0]["classification"] == "MISSING_FROM_RESTORED_DATABASE"
    assert not report["writes_released"] and report["target_writes"] == report["database_writes"] == 0
    with pytest.raises(IntegrityError, match="RECOVERY_QUALIFICATION_REQUIRED"):
        StateStore(path, tenant_id=adapter.settings.tenant_id)


def test_recovery_queries_unknown_outside_inventory_window_and_keeps_absence_unknown(target):
    server, adapter = target
    request = effect(adapter)
    with StateStore(tenant_id=adapter.settings.tenant_id, maintenance=True) as store:
        with store.transaction() as connection:
            store.put_effect(connection, effect_id=request.effect_id, target_key=request.barrier_key,
                             request_digest=request.digest, request=request.model_dump(mode="json"), created_at=SINCE)
            store.update_effect(connection, effect_id=request.effect_id, expected_state="READY",
                                state="COMMIT_UNKNOWN", updated_at=SINCE)
        quarantine(store)
        report = recovery_inventory(store, adapter, since=SINCE, until=UNTIL)
        assert report["findings"][0]["classification"] == "UNRESOLVED_NO_RECEIPT"
        assert store.get_effect(request.effect_id)["state"] == "COMMIT_UNKNOWN"
    assert report["direct_receipt_queries"] == 1 and len(server.requests) == 2


def test_recovery_budget_cannot_claim_complete_inventory(target):
    server, adapter = target
    server.page_size = 1
    receipt(server, adapter, effect(adapter, "effect:one"))
    receipt(server, adapter, effect(adapter, "effect:two"))
    with StateStore(tenant_id=adapter.settings.tenant_id, maintenance=True) as store:
        quarantine(store)
        report = recovery_inventory(store, adapter, since=SINCE, until=UNTIL, max_pages=1)
    assert report["status"] == "INCOMPLETE" and not report["target_inventory_complete"]
    assert "RECOVERY_TARGET_PAGE_BUDGET_EXHAUSTED" in report["errors"]
    assert not report["writes_released"]


def test_recovery_requires_quarantine_before_target_network_access(target):
    server, adapter = target
    with (StateStore(tenant_id=adapter.settings.tenant_id, maintenance=True) as store,
          pytest.raises(EffectError, match="RECOVERY_QUARANTINE_REQUIRED")):
        recovery_inventory(store, adapter, since=SINCE, until=UNTIL)
    assert server.requests == []


def test_report_digest_covers_review_findings(target):
    from orgrebase.digest import sha256_digest

    server, adapter = target
    receipt(server, adapter, effect(adapter))
    with StateStore(tenant_id=adapter.settings.tenant_id, maintenance=True) as store:
        quarantine(store)
        report = recovery_inventory(store, adapter, since=SINCE, until=UNTIL)
    digest = report.pop("report_digest")
    assert sha256_digest(json.loads(json.dumps(report))) == digest
    report["findings"] = []
    assert sha256_digest(report) != digest
