from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import ObjectState, VersionedObject
from orgrebase.workspace.models import (
    RuntimeDependencyManifest,
    TaskContextManifest,
    WorkspaceGraphEdge,
    WorkspaceGraphSnapshot,
)


def project(service, snapshot, artifact_reader=None):
    return service.snapshot_builder.to_enterprise_fixture(
        snapshot=snapshot, artifact_reader=artifact_reader or service.store.load_artifact,
        object_reader=service.store.get_object, agents=service.legacy.agents,
        evaluation_cases=service.legacy.evaluation_cases,
        context_profiles=service.context_profiles, change={})


def test_one_source_can_bind_two_distinct_runtime_slots(workspace_service) -> None:
    receipt = workspace_service.form_quote()
    snapshot = workspace_service.current_snapshot()
    manifest = RuntimeDependencyManifest.model_validate(
        workspace_service.store.load_artifact(receipt.dependency_manifest_ref).payload)
    entry = manifest.entries[0]
    second = entry.model_copy(update={"slot_id": "second-use-of-same-source"})
    payload = manifest.model_dump(mode="json", exclude={"digest"})
    payload["entries"].append(second.model_dump(mode="json"))
    revised = RuntimeDependencyManifest.model_validate(payload)
    edge = next(item for item in snapshot.edges if item.source_evidence_ref == entry.source_event_ref
                and item.provider_ref == entry.provider_ref)
    second_edge = edge.model_dump(mode="json", exclude={"digest"})
    second_edge["id"] = f"edge:runtime:{sha256_digest((second.provider_ref, second.consumer_ref, second.slot_id))[7:23]}"
    snapshot_payload = snapshot.model_dump(mode="json", exclude={"digest"})
    snapshot_payload["edges"].append(WorkspaceGraphEdge.model_validate(second_edge).model_dump(mode="json"))
    snapshot_payload["edges"].sort(key=lambda item: item["id"])
    snapshot_payload["edge_set_digest"] = sha256_digest(snapshot_payload["edges"])
    revised_snapshot = WorkspaceGraphSnapshot.model_validate(snapshot_payload)
    def reader(ref, media_type=None):
        if ref == manifest.ref:
            return SimpleNamespace(payload=revised.model_dump(mode="json"))
        return workspace_service.store.load_artifact(ref, media_type)
    fixture = project(workspace_service, revised_snapshot, reader)
    compiled = next(item for item in fixture.dependency_manifests
                    if item.target_id == workspace_service.quote_object_id)
    matching = [slot for slot in compiled.requirement_slots if slot.source_id == entry.provider_ref.rsplit("@", 1)[0]]
    assert len(matching) == 2
    assert len({slot.edge_id for slot in matching}) == 2
    assert len({slot.slot_id for slot in matching}) == 2


def test_current_consumer_cannot_claim_current_coverage_from_old_source_version(workspace_service) -> None:
    workspace_service.form_quote()
    snapshot = workspace_service.current_snapshot()
    source = workspace_service.store.get_object("claim:product.enterprise_plan")
    payload = source.model_dump(mode="json", exclude={"digest"})
    payload.update(version="v-current", state=ObjectState.CURRENT,
                   payload={**source.payload, "canonical_value": "Changed externally"})
    with workspace_service.store.transaction() as connection:
        workspace_service.store.insert_version(connection, VersionedObject.model_validate(payload), make_current=True)
    with pytest.raises(ValueError, match="SNAPSHOT_RUNTIME_SOURCE_VERSION_STALE"):
        project(workspace_service, snapshot)


def test_projection_reads_current_pointer_once_per_resource(workspace_service, monkeypatch) -> None:
    workspace_service.form_quote()
    snapshot = workspace_service.current_snapshot()
    expected = project(workspace_service, snapshot)
    resource_ids = {ref.rsplit("@", 1)[0] for ref in snapshot.object_refs}
    assert len(resource_ids) < len(snapshot.object_refs)
    reads: Counter[str] = Counter()
    original = workspace_service.store.get_object

    def counted_read(object_id, version=None):
        if version is None:
            reads[object_id] += 1
        return original(object_id, version)

    monkeypatch.setattr(workspace_service.store, "get_object", counted_read)
    actual = project(workspace_service, snapshot)
    assert reads == Counter({object_id: 1 for object_id in resource_ids})
    assert actual.model_dump(mode="json") == expected.model_dump(mode="json")


def test_live_projection_rejects_unspecified_source_digest_scheme(workspace_service) -> None:
    receipt = workspace_service.form_quote()
    manifest = workspace_service.store.load_artifact(receipt.dependency_manifest_ref).payload
    payload = {key: value for key, value in manifest.items() if key not in {"digest", "source_digest_scheme"}}
    historical = RuntimeDependencyManifest.model_validate(payload)
    def reader(ref, media_type=None):
        if ref == receipt.dependency_manifest_ref:
            return SimpleNamespace(payload=historical.model_dump(mode="json"))
        return workspace_service.store.load_artifact(ref, media_type)
    with pytest.raises(ValueError, match="RUNTIME_SOURCE_DIGEST_SCHEME_UNSUPPORTED"):
        project(workspace_service, workspace_service.current_snapshot(), reader)


@pytest.mark.parametrize("filename,model", [
    ("task-context.json", TaskContextManifest),
    ("runtime-dependency-manifest.json", RuntimeDependencyManifest),
])
def test_frozen_manifest_reader_preserves_historical_digest(filename, model) -> None:
    path = Path(__file__).resolve().parents[2] / "evidence/workspace/latest/formation" / filename
    raw = json.loads(path.read_text())
    assert "source_digest_scheme" not in raw
    parsed = model.model_validate(raw)
    assert parsed.model_dump(mode="json") == raw
