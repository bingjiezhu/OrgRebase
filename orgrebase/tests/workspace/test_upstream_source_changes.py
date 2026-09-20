"""Upstream validation isolates bad source values without weakening the transaction boundary."""

from __future__ import annotations

import time

import pytest

from orgrebase.auth import Principal, request_principal
from orgrebase.domain import IntegrityError, ObjectState
from orgrebase.workspace.dataverse import DataverseReader, DataverseSettings, SourceError, SourceSynchronizer
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.source_bindings import (
    SourceBindingConfig,
    SourceBindingProposal,
    binding_view,
    observe_inventory,
    propose_binding,
)
from orgrebase.workspace.source_worker import SourceFieldMapping, WorkspaceSourceAdmission
from tests.workspace.test_priced_quote_pack import priced_draft

RECORD = "00000000-0000-0000-0000-000000000001"
ORGANIZATION = "00000000-0000-0000-0000-000000000987"
INVALIDATION_MEDIA = "application/vnd.orgrebase.source-invalidation+json"


@pytest.fixture
def priced_service(tmp_path):
    seal_enterprise_quote_pilot_pack(priced_draft(tmp_path), tmp_path / "sealed")
    runtime = load_enterprise_quote_pilot_pack(tmp_path / "sealed")
    path = tmp_path / "upstream.sqlite"
    service = WorkspaceService(
        store_path=path, runtime_configuration=runtime, review_duration_seconds=0,
        store_tenant_id=runtime.profile.organization_id,
    )
    try:
        yield service, runtime, path
    finally:
        service.close()


def source_sync(service, values, monkeypatch):
    """Fake the upstream transport while retaining parsing, admission and durable cursor writes."""
    settings = DataverseSettings(
        connector_id="source:upstream-validation", tenant_id=service.profile.organization_id,
        instance_url="https://company.crm.dynamics.com", record_ids=(RECORD,),
        fields=tuple("new_" + slot for slot in values),
    )
    reader = DataverseReader(settings, lambda: pytest.fail("test must not request credentials"))
    mappings = tuple(SourceFieldMapping(record_id=RECORD, field="new_" + slot, slot_id=slot)
                     for slot in values)
    receiver = WorkspaceSourceAdmission(service, settings, mappings)
    fetched = []
    cursor = settings.endpoint + "?$deltatoken=upstream-revision"

    def fetch(previous):
        fetched.append(previous)
        return reader.parse_page({
            "@odata.deltaLink": cursor,
            "value": [{"quoteid": RECORD, "@odata.etag": 'W/"upstream-a"',
                       **{"new_" + slot: value for slot, value in values.items()}}],
        })

    monkeypatch.setattr(reader, "fetch", fetch)
    synchronizer = SourceSynchronizer(
        service.store, reader, worker_id="upstream-test", admit=receiver,
        admission_digest=receiver.admission_digest, clock=service.clock,
    )
    return synchronizer, fetched, cursor


def invalidations(service):
    return tuple(item.payload for item in service.store.list_artifacts(
        artifact_id_prefix="workspace-source-invalidation:", expected_media_type=INVALIDATION_MEDIA,
    ))


def test_priced_currency_change_preserves_amounts_and_admits_same_page_launch_date(priced_service, monkeypatch):
    service, _, _ = priced_service
    service.form_quote()
    before_quote = service.current_quote()
    before_currency = service.store.get_object("policy:finance.currency")
    before_basket = service.store.get_object("claim:product.quote_basket")
    before_date = service.store.get_object("claim:product.launch_date")
    synchronizer, fetched, cursor = source_sync(
        service, {"currency": "EUR", "launch_date": "2030-01-01"}, monkeypatch,
    )

    result = synchronizer.sync_page()
    service.changes.refresh()
    events = service.changes.all()
    assert len(events) == 1
    event = events[0]
    assert event.slot_id == "launch_date"
    assert event.operation == "UPDATE"
    assert event.proposal.payload["canonical_value"] == "2030-01-01"
    assert event.base_digest == before_date.digest
    assert service.store.get_object(before_date.id) == before_date
    currency = service.store.get_object(before_currency.id)
    assert currency.state == ObjectState.STALE
    assert currency.payload == before_currency.payload
    assert currency.digest == before_currency.digest
    assert service.store.get_object(before_basket.id) == before_basket
    assert service.current_quote().payload == before_quote.payload
    assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
    assert {gap["slot_id"] for gap in service.changes.gaps()} == {"currency"}
    receipts = invalidations(service)
    assert len(receipts) == 1
    assert receipts[0]["slot_id"] == "currency"
    assert receipts[0]["reason"] == "SOURCE_PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE"
    assert receipts[0]["source_ref"].endswith(f"({RECORD})#new_currency")
    assert result["records_admitted"] == 1
    assert result["external_writes"] == 0
    checkpoint = service.store.get_source_checkpoint(synchronizer.reader.settings.connector_id)
    assert checkpoint["cursor"] == cursor and checkpoint["revision"] == 1

    # A repeated upstream revision cannot duplicate either the candidate or its source gap.
    synchronizer.sync_page()
    assert fetched == [None, cursor]
    assert service.changes.all() == events
    assert invalidations(service) == receipts
    assert service.current_quote().payload["pricing"] == before_quote.payload["pricing"]


@pytest.mark.parametrize("slot,value,reason", [
    ("launch_date", "2030-02-30", "SOURCE_DATE_VALUE_INVALID"),
    ("currency", "usd", "SOURCE_CURRENCY_VALUE_INVALID"),
])
def test_invalid_source_value_does_not_block_other_domain_changes(
    priced_service, monkeypatch, slot, value, reason,
):
    service, _, _ = priced_service
    service.form_quote()
    before_quote = service.current_quote()
    resource = next(item for item in service.enterprise_binding.resources if item.slot_id == slot)
    before = service.store.get_object(resource.object_id)
    synchronizer, _, cursor = source_sync(
        service, {slot: value, "product_plan": "Revised upstream product plan"}, monkeypatch,
    )

    synchronizer.sync_page()
    service.changes.refresh()
    assert [(event.slot_id, event.proposal.payload["canonical_value"])
            for event in service.changes.all()] == [("product_plan", "Revised upstream product plan")]
    source = service.store.get_object(resource.object_id)
    assert source.state == ObjectState.STALE
    assert source.payload == before.payload
    assert {gap["slot_id"] for gap in service.changes.gaps()} == {slot}
    assert [(item["slot_id"], item["reason"]) for item in invalidations(service)] == [(slot, reason)]
    assert service.current_quote().payload == before_quote.payload
    assert service.store.get_source_checkpoint(synchronizer.reader.settings.connector_id)["cursor"] == cursor


def test_unexpected_integrity_failure_rolls_back_whole_page_and_replays_inbox(priced_service, monkeypatch):
    service, _, _ = priced_service
    service.form_quote()
    before_quote = service.current_quote()
    before_currency = service.store.get_object("policy:finance.currency")
    synchronizer, fetched, cursor = source_sync(service, {
        "currency": "usd", "product_plan": "New upstream plan", "launch_date": "2030-01-01",
    }, monkeypatch)
    original_insert = service.store.insert_version

    def fail_date_insert(connection, item, *, make_current):
        if item.id == "claim:product.launch_date":
            raise IntegrityError("INJECTED_SOURCE_STORE_INTEGRITY_FAILURE")
        return original_insert(connection, item, make_current=make_current)

    monkeypatch.setattr(service.store, "insert_version", fail_date_insert)
    with pytest.raises(IntegrityError, match=r"^INJECTED_SOURCE_STORE_INTEGRITY_FAILURE$"):
        synchronizer.sync_page()
    service.changes.refresh()
    assert service.changes.all() == ()
    assert invalidations(service) == ()
    assert service.changes.gaps() == ()
    assert service.current_quote() == before_quote
    assert service.store.get_object(before_currency.id) == before_currency
    assert service.store.list_artifacts(artifact_id_prefix="source-observation:") == ()
    checkpoint = service.store.get_source_checkpoint(synchronizer.reader.settings.connector_id)
    assert checkpoint["cursor"] is None and checkpoint["revision"] == 0

    monkeypatch.setattr(service.store, "insert_version", original_insert)
    synchronizer.sync_page()
    service.changes.refresh()
    assert fetched == [None]
    assert {event.slot_id for event in service.changes.all()} == {"product_plan", "launch_date"}
    assert [(item["slot_id"], item["reason"]) for item in invalidations(service)] == [
        ("currency", "SOURCE_CURRENCY_VALUE_INVALID"),
    ]
    assert service.store.get_source_checkpoint(synchronizer.reader.settings.connector_id)["cursor"] == cursor


@pytest.mark.parametrize("slot", ["quote_basket", "pricing_policy"])
def test_existing_structured_source_mapping_opens_only_its_gap_and_keeps_dates_readable(
    priced_service, monkeypatch, slot,
):
    service, _, _ = priced_service
    service.form_quote()
    resource = next(item for item in service.enterprise_binding.resources if item.slot_id == slot)
    before = service.store.get_object(resource.object_id)
    before_date = service.store.get_object("claim:product.launch_date")
    before_quote = service.current_quote()
    synchronizer, _, cursor = source_sync(
        service, {slot: '{"currency":"EUR"}', "launch_date": "2030-01-01"}, monkeypatch,
    )

    synchronizer.admit.source_unavailable(SourceError("SOURCE_SLOT_TYPE_UNSUPPORTED"))
    assert service.store.get_object(resource.object_id).state == ObjectState.STALE
    assert service.store.get_object(before_date.id) == before_date
    assert {gap["slot_id"] for gap in service.changes.gaps()} == {slot}

    synchronizer.sync_page()
    service.changes.refresh()
    assert [(event.slot_id, event.proposal.payload["canonical_value"])
            for event in service.changes.all()] == [("launch_date", "2030-01-01")]
    assert service.store.get_object(resource.object_id).payload == before.payload
    assert service.current_quote().payload == before_quote.payload
    assert [(item["slot_id"], item["reason"]) for item in invalidations(service)] == [
        (slot, "SOURCE_SLOT_TYPE_UNSUPPORTED"),
    ]
    assert service.store.get_source_checkpoint(synchronizer.reader.settings.connector_id)["cursor"] == cursor


@pytest.mark.parametrize("slot", ["quote_basket", "pricing_policy"])
def test_structured_pricing_slots_are_not_advertised_or_admitted_as_text_mappings(priced_service, slot):
    service, _, _ = priced_service
    config = SourceBindingConfig.model_validate({
        "source": {"connector_id": "source:structured-mapping", "tenant_id": service.profile.organization_id,
                   "instance_url": "https://company.crm.dynamics.com", "record_ids": [RECORD]},
        "workspace_id": service.store.workspace_id, "organization_id": ORGANIZATION,
        "source_token_variable": "TEST_SOURCE_TOKEN", "access_token_variable": "TEST_ACCESS_TOKEN",
    })
    inventory = {"config_digest": config.digest, "fields": [
        {"field": "new_" + field, "label": field, "transforms": ["identity"]}
        for field in ("currency", "launch_date", "quote_basket", "pricing_policy")
    ]}
    token = request_principal.set(Principal(
        issuer="https://test-issuer.example", subject="source-operator", tenant_id=service.profile.organization_id,
        actor_id=service.profile.default_task.actor_id, roles=frozenset({"administrator"}),
        expires_at=int(time.time()) + 300,
    ))
    try:
        current = observe_inventory(service, config, inventory)
        view = binding_view(service, config)
        candidates = {item["slot_id"] for item in view["candidates"]}
        assert {"currency", "launch_date"} <= candidates
        assert candidates.isdisjoint({"quote_basket", "pricing_policy"})
        assert {item["slot_id"] for item in view["gaps"]
                if item["code"] == "SOURCE_SLOT_TYPE_UNSUPPORTED"} == {"quote_basket", "pricing_policy"}
        before = service.store.verify_event_chain()
        with pytest.raises(SourceError, match=r"^SOURCE_SLOT_TYPE_UNSUPPORTED$"):
            propose_binding(service, config, SourceBindingProposal(
                **current, mappings=[SourceFieldMapping(record_id=RECORD, field="new_" + slot, slot_id=slot)],
            ))
        assert service.store.verify_event_chain() == before
        assert binding_view(service, config)["proposal"] is None
    finally:
        request_principal.reset(token)
