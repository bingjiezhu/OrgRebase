from __future__ import annotations

import json

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.runtime_contracts import prepare_artifact_write
from orgrebase.workspace.formation import (
    MEDIA,
    WorkspaceFormationService,
)
from orgrebase.workspace.graph import SNAPSHOT_MEDIA_TYPE, graph_pointer_object
from orgrebase.workspace.models import (
    RuntimeDependencyManifest,
    TaskReceipt,
    TraceCoverageReceipt,
    WorkspaceGraphSnapshot,
    WorkTrace,
)


def _replace_artifact(prepared, artifact_id: str, replacement):
    payload = prepared.model_dump(mode="json", exclude={"digest"})
    payload["artifact_writes"] = [
        (
            replacement.model_dump(mode="json")
            if item["artifact_id"] == artifact_id
            else item
        )
        for item in payload["artifact_writes"]
    ]
    return type(prepared).model_validate(payload)


def test_quote_does_not_exist_before_formation(workspace_service) -> None:
    assert workspace_service.state()["quote"] is None
    assert workspace_service.state()["graph_pointer"] is None


def test_atomic_formation_creates_quote_trace_manifest_and_snapshot(workspace_service) -> None:
    receipt = workspace_service.form_quote()
    quote = workspace_service.current_quote()
    assert quote.version == "v1"
    assert quote.payload["launch_date"] == "2026-09-01"
    assert quote.payload["currency"] == "USD"
    trace = WorkTrace.model_validate(workspace_service.store.load_artifact(receipt.trace_ref).payload)
    coverage = TraceCoverageReceipt.model_validate(
        workspace_service.store.load_artifact(receipt.coverage_receipt_ref).payload
    )
    manifest = RuntimeDependencyManifest.model_validate(
        workspace_service.store.load_artifact(receipt.dependency_manifest_ref).payload
    )
    assert len([event for event in trace.events if event.event_type == "REFERENCE_RESOLVED"]) == 8
    assert coverage.status == "PASS"
    assert len(manifest.entries) == 8
    assert {entry.slot_id for entry in manifest.entries} == set(coverage.observed_slots)
    pointer = workspace_service.current_graph_pointer()
    assert pointer.version == "v1"
    assert pointer.payload["snapshot_ref"] == receipt.graph_snapshot_ref


def test_formation_is_idempotent(workspace_service) -> None:
    first = workspace_service.form_quote()
    second = workspace_service.form_quote()
    assert second.digest == first.digest
    assert workspace_service.current_quote().version == "v1"
    assert workspace_service.store.get_pointer("work:quote_acme")["revision"] == 1


def test_same_idempotency_key_with_different_request_is_rejected(workspace_service) -> None:
    request = WorkspaceFormationService.default_request()
    workspace_service.form_quote(request)
    payload = request.model_dump(mode="json", exclude={"digest"})
    payload["customer_id"] = "customer:other"
    changed = type(request).model_validate(payload)
    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        workspace_service.form_quote(changed)


@pytest.mark.parametrize("attack", ("nested_owner", "actor", "organization"))
def test_cached_task_digest_cannot_bypass_formation_identity_boundary(
    workspace_service,
    attack: str,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    request = WorkspaceFormationService.default_request()
    original_digest = request.digest
    if attack == "nested_owner":
        request.input_values["owner"] = "human:attacker"
        attacked = request
    elif attack == "actor":
        attacked = request.model_copy(update={"actor_id": "human:attacker"})
    else:
        attacked = request.model_copy(update={"organization_id": "org:attacker"})
    assert attacked.digest == original_digest

    with pytest.raises(IntegrityError, match="WORKSPACE_TASK_REQUEST_MODEL_INVALID"):
        workspace_service.form_quote(attacked)
    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("organization_id", "org:other"),
        ("actor_id", "employee:other-owner"),
        ("input_values", {"owner": "employee:other-owner"}),
    ),
)
def test_direct_formation_rejects_request_outside_profile_before_write(
    workspace_service,
    field: str,
    value: object,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    request = WorkspaceFormationService.default_request()
    payload = request.model_dump(mode="json", exclude={"digest"})
    payload[field] = value
    changed = type(request).model_validate(payload)

    with pytest.raises(ValueError, match="UNSUPPORTED_SYNTHETIC_SCENARIO"):
        workspace_service.formation.form_quote(changed)

    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_prepared_formation_nested_mutation_is_rejected_before_store_write(
    workspace_service,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    prepared = workspace_service.formation.prepare_quote(
        WorkspaceFormationService.default_request()
    )
    prepared.deliverable.payload["currency"] = "BTC"

    with pytest.raises(
        IntegrityError,
        match="WORKSPACE_PREPARED_FORMATION_MODEL_INVALID",
    ):
        workspace_service.formation.commit_quote(prepared)
    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_commit_rejects_forged_request_digest_before_store_write(
    workspace_service,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    prepared = workspace_service.formation.prepare_quote(
        WorkspaceFormationService.default_request()
    )
    payload = prepared.model_dump(mode="json", exclude={"digest"})
    payload["request_digest"] = "sha256:" + ("0" * 64)
    forged = type(prepared).model_validate(payload)

    with pytest.raises(ValueError, match="UNSUPPORTED_SYNTHETIC_SCENARIO"):
        workspace_service.formation.commit_quote(forged)

    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_commit_rejects_digest_valid_deliverable_proof_graph_divergence(
    workspace_service,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    prepared = workspace_service.formation.prepare_quote(
        WorkspaceFormationService.default_request()
    )
    quote_payload = prepared.deliverable.model_dump(mode="json", exclude={"digest"})
    quote_payload["payload"]["currency"] = "BTC"
    forged_quote = type(prepared.deliverable).model_validate(quote_payload)
    bundle_payload = prepared.model_dump(mode="json", exclude={"digest"})
    bundle_payload["deliverable"] = forged_quote.model_dump(mode="json")
    forged = type(prepared).model_validate(bundle_payload)

    with pytest.raises(
        IntegrityError,
        match="PREPARED_FORMATION_PROOF_GRAPH_INVALID:DELIVERABLE_REPLAY_MISMATCH",
    ):
        workspace_service.formation.commit_quote(forged)

    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_commit_rejects_digest_valid_snapshot_pointer_event_divergence(
    workspace_service,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    prepared = workspace_service.formation.prepare_quote(
        WorkspaceFormationService.default_request()
    )
    snapshot_write = next(
        item
        for item in prepared.artifact_writes
        if item.media_type == SNAPSHOT_MEDIA_TYPE
    )
    snapshot_payload = json.loads(json.dumps(snapshot_write.payload))
    snapshot_payload.pop("digest")
    quote_binding = next(
        item
        for item in snapshot_payload["object_digests"]
        if item["object_ref"] == prepared.deliverable.ref
    )
    quote_binding["digest"] = "sha256:" + ("0" * 64)
    forged_snapshot = WorkspaceGraphSnapshot.model_validate(snapshot_payload)
    forged_snapshot_write = prepare_artifact_write(
        forged_snapshot.ref,
        SNAPSHOT_MEDIA_TYPE,
        forged_snapshot,
    )
    forged = _replace_artifact(
        prepared,
        forged_snapshot.ref,
        forged_snapshot_write,
    )
    bundle_payload = forged.model_dump(mode="json", exclude={"digest"})
    bundle_payload["graph_pointer"] = graph_pointer_object(
        version=forged_snapshot.version,
        snapshot=forged_snapshot,
        promoted_at=prepared.task_receipt.committed_at,
    ).model_dump(mode="json")
    bundle_payload["event_payload"]["snapshot_digest"] = forged_snapshot.digest
    forged = type(prepared).model_validate(bundle_payload)

    with pytest.raises(
        IntegrityError,
        match="PREPARED_FORMATION_PROOF_GRAPH_INVALID:SNAPSHOT_REPLAY_MISMATCH",
    ):
        workspace_service.formation.commit_quote(forged)

    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_commit_rejects_digest_valid_receipt_artifact_divergence(
    workspace_service,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    prepared = workspace_service.formation.prepare_quote(
        WorkspaceFormationService.default_request()
    )
    receipt_payload = prepared.task_receipt.model_dump(mode="json", exclude={"digest"})
    receipt_payload["committed_at"] = "2026-08-15T00:00:01Z"
    alternate_receipt = TaskReceipt.model_validate(receipt_payload)
    alternate_write = prepare_artifact_write(
        alternate_receipt.id,
        MEDIA["receipt"],
        alternate_receipt,
    )
    forged = _replace_artifact(
        prepared,
        alternate_receipt.id,
        alternate_write,
    )

    with pytest.raises(
        IntegrityError,
        match="PREPARED_FORMATION_PROOF_GRAPH_INVALID:TASK_RECEIPT_ARTIFACT_MISMATCH",
    ):
        workspace_service.formation.commit_quote(forged)

    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_commit_rejects_duplicate_artifact_id_before_store_write(
    workspace_service,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    prepared = workspace_service.formation.prepare_quote(
        WorkspaceFormationService.default_request()
    )
    bundle_payload = prepared.model_dump(mode="json", exclude={"digest"})
    bundle_payload["artifact_writes"].append(bundle_payload["artifact_writes"][0])
    forged = type(prepared).model_validate(bundle_payload)

    with pytest.raises(
        IntegrityError,
        match="PREPARED_FORMATION_PROOF_GRAPH_INVALID:DUPLICATE_ARTIFACT_ID",
    ):
        workspace_service.formation.commit_quote(forged)

    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_event_metadata_cannot_override_formation_commit_facts(
    workspace_service,
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    formation = workspace_service.formation
    request = formation.default_request()
    template, _, interpretation, revision_lock, coalition = (
        formation.compile_quote_contracts(request)
    )
    projections = formation.domain_registry.source_projections(
        task=request,
        template=template,
        plan=coalition,
        now=formation.clock.now(),
    )
    candidates, bundles = formation.domain_registry.execute_selected(
        task=request,
        template=template,
        plan=coalition,
        projections=projections,
        now=formation.clock.now(),
    )

    with pytest.raises(
        ValueError,
        match="FORMATION_EVENT_METADATA_RESERVED:snapshot_digest",
    ):
        formation.prepare_quote_from_candidates(
            request=request,
            template=template,
            interpretation=interpretation,
            revision_lock=revision_lock,
            coalition=coalition,
            source_projections=projections,
            claim_candidates=candidates,
            bundles=bundles,
            event_metadata={"snapshot_digest": "sha256:" + ("0" * 64)},
        )

    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == before_chain


def test_formation_failure_rolls_back_all_business_writes(workspace_service, monkeypatch) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    original = workspace_service.store.save_artifact
    calls = {"count": 0}

    def fail_after_some(connection, artifact_id, media_type, payload):
        calls["count"] += 1
        if calls["count"] == 5:
            raise RuntimeError("INJECTED_FORMATION_FAILURE")
        return original(connection, artifact_id, media_type, payload)

    monkeypatch.setattr(workspace_service.store, "save_artifact", fail_after_some)
    with pytest.raises(RuntimeError, match="INJECTED_FORMATION_FAILURE"):
        workspace_service.form_quote()
    with pytest.raises(KeyError):
        workspace_service.store.get_object("work:quote_acme")
    with pytest.raises(KeyError):
        workspace_service.store.get_object("graph:workspace")
    assert not workspace_service.store.artifact_exists("work-trace:quote_acme@v1")
    assert workspace_service.store.verify_event_chain() == before_chain


def test_trace_payload_has_no_canary_or_restricted_source(formed_service) -> None:
    trace = formed_service.store.load_artifact("work-trace:quote_acme@v1").payload
    rendered = json.dumps(trace, ensure_ascii=False)
    assert "ORGREBASE_CANARY_SECRET_" not in rendered
    assert "SYNTHETIC RESTRICTED SOURCE" not in rendered
