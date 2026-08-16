from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.models import TaskRequest


def test_workspace_contract_schemas_are_generated_from_registry() -> None:
    from scripts.export_contract_schemas import registered_models

    models = registered_models()
    assert len(models) == 58
    assert "workspace-task-request.schema.json" in models
    assert "tool-invocation-receipt.schema.json" in models
    schema = models["workspace-task-request.schema.json"].model_json_schema()
    assert schema["additionalProperties"] is False


def test_content_address_rejects_tampered_digest() -> None:
    request = TaskRequest(
        id="task:test",
        organization_id="org:northstar",
        actor_id="employee:test",
        purpose="enterprise_quote",
        deliverable_kind="QUOTE",
        requested_at="2026-08-15T00:00:00Z",
        template_ref="template:enterprise_quote@v1",
        input_values={},
        customer_id="customer:test",
        idempotency_key="test@1",
    )
    payload = request.model_dump(mode="json")
    payload["purpose"] = "other"
    with pytest.raises(ValueError, match="content digest mismatch"):
        TaskRequest.model_validate(payload)


def test_verified_artifact_read_is_copy_and_media_bound(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "store.sqlite")
    try:
        with store.transaction() as connection:
            digest = store.save_artifact(connection, "artifact:a", "application/json", {"x": 1})
        first = store.load_artifact("artifact:a", "application/json")
        first.payload["x"] = 9
        second = store.load_artifact("artifact:a", "application/json")
        assert second.payload == {"x": 1}
        assert second.payload_digest == digest
        with pytest.raises(IntegrityError, match="ARTIFACT_MEDIA_TYPE_MISMATCH"):
            store.load_artifact("artifact:a", "text/plain")
    finally:
        store.close()


def test_verified_artifact_read_detects_database_tamper(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "store.sqlite")
    try:
        with store.transaction() as connection:
            store.save_artifact(connection, "artifact:a", "application/json", {"x": 1})
        store.connection.execute(
            "UPDATE artifacts SET payload_json=? WHERE artifact_id=?",
            (canonical_json({"x": 2}), "artifact:a"),
        )
        store.connection.commit()
        with pytest.raises(IntegrityError, match="ARTIFACT_DIGEST_MISMATCH"):
            store.load_artifact("artifact:a")
    finally:
        store.close()


def test_prepared_artifact_bundle_verifies_payload_digest(tmp_path: Path) -> None:
    from orgrebase.workspace.models import ArtifactWrite

    store = StateStore(tmp_path / "store.sqlite")
    bad = ArtifactWrite(
        artifact_id="artifact:a",
        media_type="application/json",
        payload={"x": 1},
        payload_digest=sha256_digest({"x": 1}),
    )
    object.__setattr__(bad, "payload_digest", sha256_digest({"x": 2}))
    try:
        with (
            pytest.raises(IntegrityError, match="ARTIFACT_PREPARED_DIGEST_MISMATCH"),
            store.transaction() as connection,
        ):
            store.save_artifact_writes(connection, (bad,))
    finally:
        store.close()
