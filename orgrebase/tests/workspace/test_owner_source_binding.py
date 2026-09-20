from __future__ import annotations

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError
from orgrebase.workspace.dataverse import SourceError
from orgrebase.workspace.enterprise_binding import (
    binding_revision,
    require_change_owner,
    resource_authority,
    seed_binding,
)
from orgrebase.workspace.owner_change import activate_owner_change
from orgrebase.workspace.source_bindings import (
    MEDIA,
    SourceBindingConfig,
    SourceBindingConfirmation,
    SourceBindingProposal,
    SourceFieldMapping,
    active_binding,
    confirm_binding,
    current_inventory,
    current_proposal,
    observe_inventory,
    propose_binding,
    source_coverage,
)
from orgrebase.workspace.source_worker import WorkspaceSourceAdmission
from tests.workspace.test_owner_migration import confirmed
from tests.workspace.test_owner_migration import migration_workspace as migration_workspace

RECORD = "00000000-0000-0000-0000-000000000001"
FIELD = "new_product_plan"
SLOT = "product_plan"


@pytest.fixture
def owner_source(migration_workspace):
    workspace, principal, *_ = migration_workspace
    config = SourceBindingConfig.model_validate({
        "source": {"connector_id": "source:owner-handover", "tenant_id": workspace.profile.organization_id,
                   "instance_url": "https://company.crm.dynamics.com", "record_ids": [RECORD]},
        "organization_id": "00000000-0000-0000-0000-000000000987",
        "source_token_variable": "UNUSED_SOURCE_TOKEN", "access_token_variable": "UNUSED_ACCESS_TOKEN",
    })
    inventory = {"schema_version": "orgrebase.source-inventory.v1", "config_digest": config.digest,
                 "organization_id": config.organization_id,
                 "fields": [{"field": FIELD, "label": "Product plan", "transforms": ["identity"]}]}
    mapping = SourceFieldMapping(record_id=RECORD, field=FIELD, slot_id=SLOT)
    with principal("operator"):
        observed = observe_inventory(workspace, config, inventory)
        view = propose_binding(workspace, config, SourceBindingProposal(**observed, mappings=[mapping]))
    digest = view["proposal"]["proposal_digest"]
    with principal(SLOT):
        confirm_binding(workspace, config, digest[7:], SourceBindingConfirmation(proposal_digest=digest))
    settings = config.source.reader_settings((FIELD,))
    receiver = WorkspaceSourceAdmission(workspace, settings, (mapping,))
    record = {"record_id": RECORD, "revision": 'W/"same-observation"', "deleted": False,
              "fields": {FIELD: "Renewed source service plan"}}
    return workspace, principal, config, inventory, observed, mapping, settings, receiver, record


def _handover(workspace, principal, *, back=False):
    old_owner = next(r.owner_id for r in workspace.enterprise_binding.resources if r.slot_id == SLOT)
    kwargs = {}
    if back:
        kwargs = {"proposal_id": "return-owner", "old": "successor", "successor": SLOT,
                  "actor": next(r.owner_id for r in seed_binding(workspace).resources if r.slot_id == SLOT)}
    command = confirmed(workspace, principal, **kwargs)
    with principal(SLOT if back else "successor"):
        activate_owner_change(workspace, kwargs.get("proposal_id", "handover"), command)
    assert next(r.owner_id for r in workspace.enterprise_binding.resources if r.slot_id == SLOT) != old_owner


def _generation(workspace, observed):
    return workspace.store.load_artifact("source-generation:" + observed["generation_digest"][7:], MEDIA).payload


def _receive(workspace, principal, receiver, record):
    with principal("operator"), workspace.store.transaction() as connection:
        receiver(connection, record, workspace.clock.now(), "source-page:one")


def test_initial_inventory_and_source_event_keep_existing_identity(owner_source):
    workspace, principal, config, inventory, observed, _, settings, receiver, record = owner_source
    generation = _generation(workspace, observed)
    assert generation == {
        "inventory_digest": sha256_digest(inventory), "config_digest": config.digest,
        "enterprise_binding_digest": workspace.enterprise_binding.digest,
        "previous_generation": None, "observed_at": generation["observed_at"],
    }
    assert sha256_digest(generation) == observed["generation_digest"]
    with principal("operator"):
        assert observe_inventory(workspace, config, inventory) == observed
    current = workspace.store.get_object(receiver.resources[SLOT].object_id)
    observation = sha256_digest({"connector": settings.connector_id, "record": RECORD,
                                "revision": record["revision"], "field": FIELD})[7:]
    expected_event = "source:" + sha256_digest({"observation": observation, "base": current.ref,
                                               "operation": "UPDATE"})[7:]
    _receive(workspace, principal, receiver, record)
    _receive(workspace, principal, receiver, record)
    assert [event.event_id for event in workspace.changes.all()] == [expected_event]


@pytest.mark.parametrize("round_trip", [False, True])
def test_same_inventory_requires_new_generation_and_owner_confirmation(owner_source, round_trip):
    workspace, principal, config, inventory, observed, mapping, *_ = owner_source
    seed_digest = workspace.enterprise_binding.digest
    original_generation = _generation(workspace, observed)
    _handover(workspace, principal)
    if round_trip:
        _handover(workspace, principal, back=True)
        assert workspace.enterprise_binding.digest == seed_digest
    with principal("operator"):
        with pytest.raises(SourceError, match="SOURCE_DISCOVERY_REQUIRED"):
            current_inventory(workspace, config)
        assert source_coverage(workspace, config)["status"] == "UNKNOWN"
        replacement = observe_inventory(workspace, config, inventory)
        assert replacement["inventory_digest"] == observed["inventory_digest"]
        assert replacement["generation_digest"] != observed["generation_digest"]
        assert _generation(workspace, replacement)["binding_revision"] == binding_revision(workspace)
        assert _generation(workspace, observed) == original_generation
        with pytest.raises(SourceError, match="SOURCE_MAPPING_RECONFIRMATION_REQUIRED"):
            current_proposal(workspace, config)
        view = propose_binding(workspace, config, SourceBindingProposal(**replacement, mappings=[mapping]))
        assert view["status"] == "CONFIRMATION_REQUIRED"
    digest = view["proposal"]["proposal_digest"]
    command = SourceBindingConfirmation(proposal_digest=digest)
    with (principal("successor" if round_trip else SLOT),
          pytest.raises(AuthorizationError, match="SOURCE_ORIGINAL_OWNER_REQUIRED")):
        confirm_binding(workspace, config, digest[7:], command)
    with principal(SLOT if round_trip else "successor"):
        confirm_binding(workspace, config, digest[7:], command)
        assert active_binding(workspace, config)["generation_digest"] == replacement["generation_digest"]


@pytest.mark.parametrize("operation", ["receive", "source_unavailable"])
@pytest.mark.parametrize("round_trip", [False, True])
def test_pre_migration_worker_cannot_write_after_owner_changes(owner_source, operation, round_trip):
    workspace, principal, _, _, _, _, _, receiver, record = owner_source
    _handover(workspace, principal)
    if round_trip:
        _handover(workspace, principal, back=True)
    before = tuple(workspace.store.connection.iterdump())
    with pytest.raises(SourceError, match="SOURCE_MAPPING_RECONFIRMATION_REQUIRED"):
        if operation == "receive":
            _receive(workspace, principal, receiver, record)
        else:
            receiver.source_unavailable(SourceError("SOURCE_PERMISSION_DENIED"))
    assert tuple(workspace.store.connection.iterdump()) == before


def test_successor_reuses_original_observation_without_reusing_old_event(owner_source):
    workspace, principal, _, _, _, mapping, settings, receiver, record = owner_source
    _receive(workspace, principal, receiver, record)
    old_event = workspace.changes.all()[0]
    observation_id = old_event.proposal.payload["source_observation_ref"]
    observation = workspace.store.load_artifact(observation_id, "application/json")
    old_authority = resource_authority(workspace, SLOT)
    _handover(workspace, principal)
    current = workspace.store.get_object(receiver.resources[SLOT].object_id)
    fresh = WorkspaceSourceAdmission(workspace, settings, (mapping,))
    _receive(workspace, principal, fresh, record)
    _receive(workspace, principal, fresh, record)
    events = workspace.changes.all()
    assert len(events) == 2
    new_event = next(event for event in events if event.event_id != old_event.event_id)
    assert new_event.owner_id == "user:successor"
    assert resource_authority(workspace, SLOT) != old_authority
    assert new_event.event_id == "source:" + sha256_digest({
        "observation": observation_id.split(":", 1)[1], "base": current.ref, "operation": "UPDATE",
        "resource_authority": resource_authority(workspace, SLOT),
    })[7:]
    assert workspace.changes.get(old_event.event_id) == old_event
    assert new_event.proposal.payload["source_observation_ref"] == observation_id
    assert workspace.store.load_artifact(observation_id, "application/json") == observation


def test_unmigrated_slot_keeps_original_event_identity_and_pending_count(owner_source):
    workspace, principal, config, _, _, _, _, _, _ = owner_source
    mapping = SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency")
    settings = config.source.reader_settings(("new_currency",))
    record = {"record_id": RECORD, "revision": 'W/"currency-observation"', "deleted": False,
              "fields": {"new_currency": "EUR"}}
    receiver = WorkspaceSourceAdmission(workspace, settings, (mapping,))
    _receive(workspace, principal, receiver, record)
    original = workspace.changes.all()[0]
    authority = resource_authority(workspace, "currency")
    observation = workspace.store.load_artifact(original.proposal.payload["source_observation_ref"], "application/json")
    _handover(workspace, principal)
    assert resource_authority(workspace, "currency") == authority
    workspace.changes.refresh()
    pending_count = workspace.changes.pending_count
    fresh = WorkspaceSourceAdmission(workspace, settings, (mapping,))
    _receive(workspace, principal, fresh, record)
    _receive(workspace, principal, fresh, record)
    assert workspace.changes.all() == (original,)
    workspace.changes.refresh()
    assert workspace.changes.pending_count == pending_count == 1
    require_change_owner(workspace, original)
    assert workspace.store.load_artifact(original.proposal.payload["source_observation_ref"], "application/json") == observation
