"""Change admission cannot turn slot updates into an untyped evidence channel."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.workspace.models import ChangeEvent


def proposal(workspace, event_id):
    event = workspace.changes.get("launch_date").model_dump(mode="json", exclude={"digest"})
    event["event_id"] = event_id
    event["proposal"].pop("digest")
    event["proposal"]["version"] = "proposal-" + event_id
    return event


@pytest.mark.parametrize("mutation", ["extra", "nested", "remove", "dependencies"])
def test_raw_change_rejects_payload_injection_before_any_persistence(workspace_service, mutation):
    workspace = workspace_service
    payload = proposal(workspace, "rejected-" + mutation)
    body = payload["proposal"]["payload"]
    if mutation == "extra":
        body["customer_secret"] = "PRIVATE_SENTINEL"
    elif mutation == "nested":
        body["authority"] = {"customer_secret": "PRIVATE_SENTINEL"}
    elif mutation == "remove":
        body.pop("authority")
    else:
        body["read_dependencies"] = {"customer_secret": "PRIVATE_SENTINEL"}
    before = deepcopy(workspace.store.event_envelopes())
    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.post("/api/workspace/changes", json=payload)
    assert response.status_code in (400, 409, 422)
    assert "PRIVATE_SENTINEL" not in response.text
    assert workspace.store.event_envelopes() == before
    with pytest.raises(KeyError):
        workspace.changes.get(payload["event_id"])
    with pytest.raises(KeyError):
        workspace.store.get_object(payload["proposal"]["id"], payload["proposal"]["version"])


def test_raw_change_preserves_admitted_metadata(workspace_service):
    payload = proposal(workspace_service, "metadata-preserved")
    event = ChangeEvent.model_validate(payload)
    result = workspace_service.register_change(event)
    assert result["event_digest"] == event.digest
    assert workspace_service.changes.get(event.event_id).proposal.payload == event.proposal.payload


@pytest.mark.parametrize("event_type,key", [
    ("WORKSPACE_CHANGE_REJECTED", "event_id"),
    ("WORKSPACE_CHANGE_OUTCOME_RECORDED", "kind"),
])
def test_unbound_completion_event_cannot_close_change(workspace_service, event_type, key):
    from orgrebase.domain import IntegrityError
    event = workspace_service.changes.get("launch_date")
    before = workspace_service.store.history_projection()
    with pytest.raises(IntegrityError), workspace_service.store.transaction() as connection:
        workspace_service.store.append_event(connection, event_type, {key: event.event_id})
    assert workspace_service.store.history_projection() == before
    assert workspace_service.changes.pending_count == before["pending_change_count"]


def test_wrong_rejection_artifact_cannot_close_change(workspace_service):
    from orgrebase.domain import IntegrityError
    event = workspace_service.changes.get("launch_date")
    payload = {"event_id": event.event_id, "event_digest": "sha256:" + "0" * 64,
               "actor_id": event.owner_id, "reason": "Declined", "status": "REJECTED"}
    before = workspace_service.store.history_projection()
    with pytest.raises(IntegrityError), workspace_service.store.transaction() as connection:
        workspace_service.store.save_artifact(connection, "workspace-rejection:launch_date@r1",
                                             "application/vnd.orgrebase.change-rejection+json", payload)
        workspace_service.store.append_event(connection, "WORKSPACE_CHANGE_REJECTED", payload)
    assert workspace_service.store.history_projection() == before


def test_wrong_outcome_artifact_cannot_close_change(workspace_service):
    from orgrebase.domain import IntegrityError
    event = workspace_service.changes.get("launch_date")
    before = workspace_service.store.history_projection()
    with pytest.raises(IntegrityError), workspace_service.store.transaction() as connection:
        digest = workspace_service.store.save_artifact(
            connection, "workspace-outcome:launch_date@r1",
            "application/vnd.orgrebase.workspace-apply-outcome+json",
            {"kind": "another-change", "change_spec": {}, "quote": {}},
        )
        workspace_service.store.append_event(connection, "WORKSPACE_CHANGE_OUTCOME_RECORDED", {
            "kind": event.event_id, "artifact_id": "workspace-outcome:launch_date@r1",
            "artifact_digest": digest, "approval_digest": "sha256:" + "0" * 64, "quote_ref": "unrelated@v2",
        })
    assert workspace_service.store.history_projection() == before


@pytest.mark.parametrize("reference", ["PRIVATE_SENTINEL", "source-observation:" + "0" * 64])
def test_raw_change_cannot_invent_a_connector_observation(workspace_service, reference):
    from orgrebase.domain import IntegrityError
    payload = proposal(workspace_service, "false-observation")
    payload["proposal"]["payload"]["source_observation_ref"] = reference
    before = workspace_service.store.history_projection()
    with pytest.raises(IntegrityError, match="CHANGE_EVENT_SOURCE_OBSERVATION_INVALID"):
        workspace_service.register_change(ChangeEvent.model_validate(payload))
    assert workspace_service.store.history_projection() == before


def admitted_source_observation(workspace):
    """Seed the persisted state left by an admitted connector source version."""
    from orgrebase.digest import sha256_digest
    from orgrebase.domain import VersionedObject

    current = workspace.store.get_object("claim:product.launch_date")
    source_ref, page_ref = "source:connector-product-date", "source-page:product-date"
    observation = {"value_digest": sha256_digest(current.payload["canonical_value"]),
                   "source_ref": source_ref, "page_ref": page_ref,
                   "observed_at": current.valid_from, "revision": "source-revision-1"}
    reference = "source-observation:" + sha256_digest(observation)[7:]
    source = VersionedObject.model_validate({
        **current.model_dump(mode="json", exclude={"digest"}),
        "version": "admitted-connector-1",
        "source_refs": [source_ref, page_ref],
        "payload": {**current.payload, "source_observation_ref": reference},
    })
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(connection, reference, "application/json", observation)
        workspace.store.insert_version(connection, source, make_current=True)
    return source, reference, observation


def test_manual_change_does_not_relabel_old_connector_observation(workspace_service):
    from orgrebase.workspace.change_proposals import ChangeProposalInput, submit_change

    current, reference, observation = admitted_source_observation(workspace_service)
    result = submit_change(workspace_service, ChangeProposalInput(
        event_id="manual-after-connector", slot_id="launch_date", value="2031-11-05",
        base_version=current.version, base_digest=current.digest,
        source_ref="source:manual-product-decision",
    ))
    proposal = result["event"]["proposal"]
    assert proposal["payload"]["canonical_value"] == "2031-11-05"
    assert "source_observation_ref" not in proposal["payload"]
    assert proposal["source_refs"] == ["source:manual-product-decision"]
    assert workspace_service.store.get_object(current.id) == current
    assert workspace_service.store.load_artifact(reference, "application/json").payload == observation


@pytest.mark.parametrize("mutation", ["value", "source", "page", "time"])
def test_raw_change_cannot_reuse_mismatched_current_observation(workspace_service, mutation):
    from orgrebase.domain import IntegrityError, ObjectState

    current, _, _ = admitted_source_observation(workspace_service)
    body = current.model_dump(mode="json", exclude={"digest"})
    body.update(version="proposal-reused-observation", state=ObjectState.PROPOSED)
    if mutation == "value":
        body["payload"]["canonical_value"] = "2031-11-05"
    elif mutation == "source":
        body["source_refs"] = body["source_refs"][1:]
    elif mutation == "page":
        body["source_refs"] = body["source_refs"][:1]
    else:
        body["valid_from"] = "2030-01-01T00:00:00Z"
    event = ChangeEvent(
        event_id="reuse-observation-" + mutation,
        organization_id=workspace_service.profile.organization_id,
        slot_id="launch_date", owner_id=workspace_service.change_owner["launch_date"],
        base_version=current.version, base_digest=current.digest, proposal=body,
        occurred_at=workspace_service.clock.now(),
    )
    before = workspace_service.store.history_projection()
    with pytest.raises(IntegrityError, match="CHANGE_EVENT_SOURCE_OBSERVATION_INVALID"):
        workspace_service.register_change(event, _source_observation_admission=True)
    assert workspace_service.store.history_projection() == before
    with pytest.raises(KeyError):
        workspace_service.store.get_object(current.id, event.proposal.version)


def test_raw_http_change_cannot_claim_even_an_existing_connector_observation(workspace_service):
    current, _, _ = admitted_source_observation(workspace_service)
    body = current.model_dump(mode="json", exclude={"digest"})
    body.update(version="proposal-client-claimed-observation", state="PROPOSED")
    event = ChangeEvent(
        event_id="client-claimed-observation", organization_id=workspace_service.profile.organization_id,
        slot_id="launch_date", owner_id=workspace_service.change_owner["launch_date"],
        base_version=current.version, base_digest=current.digest, proposal=body,
        occurred_at=workspace_service.clock.now(),
    )
    before = workspace_service.store.history_projection()
    with TestClient(create_app(workspace_service=workspace_service)) as client:
        response = client.post("/api/workspace/changes", json=event.model_dump(mode="json"))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EVIDENCE_INTEGRITY_FAILED"
    assert workspace_service.store.history_projection() == before
