from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.change_events import ChangeEvent as RuntimeChangeEvent
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.runtime_contracts import ArtifactWrite as RuntimeArtifactWrite
from orgrebase.runtime_contracts import StoredArtifact as RuntimeStoredArtifact
from orgrebase.runtime_contracts import WorkflowIdentity as RuntimeWorkflowIdentity
from orgrebase.store import StateStore
from orgrebase.workspace.models import (
    ArtifactWrite,
    ChangeEvent,
    StoredArtifact,
    TaskRequest,
    WorkflowIdentity,
)


def test_workspace_contract_schemas_are_generated_from_registry() -> None:
    from scripts.export_contract_schemas import LEGACY_MODELS, registered_models

    models = registered_models()
    legacy_schema_names = set(LEGACY_MODELS)
    workspace_schema_names = set(models) - legacy_schema_names

    assert len(legacy_schema_names) == 5
    assert len(workspace_schema_names) == 133
    assert len(models) == len(legacy_schema_names) + len(workspace_schema_names) == 138
    assert {"workspace-model-request.schema.json", "workspace-model-response-receipt.schema.json",
            "workspace-model-input-projection.schema.json", "workspace-model-request-v2.schema.json",
            "workspace-model-response-receipt-v2.schema.json"} <= workspace_schema_names
    assert {"workspace-change-event.schema.json", "workspace-domain-pack.schema.json",
            "workspace-enterprise-binding.schema.json", "workspace-enterprise-resource-binding.schema.json"} <= workspace_schema_names
    assert "workspace-task-request.schema.json" in models
    assert "workspace-enterprise-seed-profile.schema.json" in models
    assert "workspace-enterprise-seed-admission-receipt.schema.json" in models
    assert "workspace-enterprise-seed-component-root.schema.json" in models
    assert "workspace-enterprise-seed-source-admission-receipt.schema.json" in models
    assert "workspace-enterprise-seed-runtime-projection-receipt.schema.json" in models
    assert "workspace-enterprise-quote-pilot-pack.schema.json" in models
    assert "workspace-pilot-domain-projection.schema.json" in models
    assert "workspace-pilot-knowledge-projection.schema.json" in models
    assert "workspace-pilot-authority-projection.schema.json" in models
    assert "workspace-pilot-capability-projection.schema.json" in models
    assert "workspace-pilot-dependency-projection.schema.json" in models
    assert "workspace-o-a-c-dependency-projection-receipt.schema.json" in models
    assert "workspace-controlled-h-t-t-p-receipt.schema.json" in models
    assert "workspace-telemetry-query-receipt.schema.json" in models
    assert "workspace-alert-evaluation-receipt.schema.json" in models
    assert "workspace-retention-receipt.schema.json" in models
    assert "workspace-backup-restore-receipt.schema.json" in models
    assert "workspace-capacity-smoke-receipt.schema.json" in models
    assert "workspace-task-agent-context-envelope.schema.json" in models
    assert "workspace-task-formation-decision-receipt.schema.json" in models
    assert "workspace-o-a-c-owner-review-summary.schema.json" in models
    assert "workspace-candidate-promotion-bundle.schema.json" in models
    assert "workspace-promotion-preview-binding.schema.json" in models
    assert "workspace-promotion-approval.schema.json" in models
    assert "workspace-promotion-apply-receipt.schema.json" in models
    assert "tool-invocation-receipt.schema.json" in models
    schema = models["workspace-task-request.schema.json"].model_json_schema()
    assert schema["additionalProperties"] is False



def test_enterprise_public_contracts_are_exported_from_their_single_model_owner() -> None:
    import importlib

    from scripts.export_contract_schemas import registered_models

    expected = {
        "orgrebase.domain": {
            "workspace-source-approval.schema.json": "SourceApproval",
            "workspace-rebase-approval-set.schema.json": "RebaseApprovalSet",
            "workspace-rebase-receipt.schema.json": "RebaseReceipt",
        },
        "orgrebase.workspace.source_readmission": {
            "workspace-source-event-ref.schema.json": "SourceEventRef",
            "workspace-source-readmission-input.schema.json": "SourceReadmissionInput",
            "workspace-source-readmission-command.schema.json": "SourceReadmissionCommand",
            "workspace-source-readmission-group.schema.json": "SourceReadmissionGroup",
        },
        "orgrebase.workspace.read_dependencies": {
            "workspace-read-query.schema.json": "ReadQuery",
            "workspace-read-witness.schema.json": "ReadWitness",
            "workspace-read-dependencies.schema.json": "ReadDependencies",
        },
        "orgrebase.workspace.source_bindings": {
            "workspace-source-field-mapping.schema.json": "SourceFieldMapping",
            "workspace-source-binding-proposal.schema.json": "SourceBindingProposal",
            "workspace-source-binding-confirmation.schema.json": "SourceBindingConfirmation",
            "workspace-source-binding-config.schema.json": "SourceBindingConfig",
        },
        "orgrebase.workspace.change_proposals": {
            "workspace-change-proposal-input.schema.json": "ChangeProposalInput",
            "workspace-review-observation-input.schema.json": "ReviewObservationInput",
        },
        "orgrebase.workspace.approval_authority": {
            "workspace-delegation-input.schema.json": "DelegationInput",
            "workspace-coordination-input.schema.json": "CoordinationInput",
        },
        "orgrebase.workspace.effects": {
            "workspace-effect-proposal-input.schema.json": "EffectProposalInput",
            "workspace-effect-approval-input.schema.json": "EffectApprovalInput",
            "workspace-effect-action-input.schema.json": "EffectActionInput",
            "workspace-effect-worker-config.schema.json": "EffectWorkerConfig",
        },
    }
    models = registered_models()
    assert sum(len(values) for values in expected.values()) == 22
    for module, values in expected.items():
        owner = importlib.import_module(module)
        for filename, name in values.items():
            assert models[filename] is getattr(owner, name)
            schema = models[filename].model_json_schema(mode="validation")
            assert schema["additionalProperties"] is False
    assert models["workspace-change-event.schema.json"] is RuntimeChangeEvent
    proposal = models["workspace-change-proposal-input.schema.json"].model_json_schema(mode="validation")
    assert proposal["properties"]["read_dependencies"]["anyOf"] == [
        {"$ref": "#/$defs/ReadDependencies"}, {"type": "null"},
    ]
    assert proposal["$defs"]["ReadDependencies"]["additionalProperties"] is False
    assert proposal["$defs"]["ReadDependencies"]["properties"]["witnesses"]["minItems"] == 1

def test_workspace_keeps_compatible_runtime_contract_reexports() -> None:
    assert ChangeEvent is RuntimeChangeEvent
    assert ArtifactWrite is RuntimeArtifactWrite
    assert StoredArtifact is RuntimeStoredArtifact
    assert WorkflowIdentity is RuntimeWorkflowIdentity


def test_state_store_tightens_file_permissions(tmp_path: Path) -> None:
    database = tmp_path / "store.sqlite"
    database.touch(mode=0o644)
    database.chmod(0o644)

    with StateStore(database):
        assert database.stat().st_mode & 0o777 == 0o600


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
