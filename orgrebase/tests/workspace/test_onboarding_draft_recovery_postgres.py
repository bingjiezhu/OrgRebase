from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.store import StateStore
from orgrebase.workspace.onboarding_drafts import (
    CreateOnboardingDraft,
    SealOnboardingDraft,
    create_draft,
    export_sealed_draft,
    read_draft,
    read_operation,
    seal_draft,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_onboarding_draft_recovery import draft_files, governor


def test_postgres_private_draft_and_operation_recover_after_restart(
    postgres_runtime,
    tmp_path: Path,
) -> None:
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    credentials = postgres_runtime(tenant_id=runtime.profile.organization_id)
    kwargs = {
        "runtime_configuration": runtime,
        "store_path": credentials["runtime_dsn"],
        "store_tenant_id": runtime.profile.organization_id,
        "store_migrate": False,
    }
    first = WorkspaceService(**kwargs)
    try:
        with governor(first):
            files = draft_files()
            del files["components/knowledge.json"]["projection_digest"]
            created = create_draft(
                first,
                CreateOnboardingDraft(
                    operation_key="postgres-create",
                    draft_id="postgres-draft",
                    files=files,
                ),
            )
            sealed = seal_draft(first, SealOnboardingDraft(
                operation_key="postgres-seal", draft_id="postgres-draft", revision=1,
                draft_receipt_digest=created["receipt_digest"],
            ))
    finally:
        first.close()

    second = WorkspaceService(**kwargs)
    try:
        with governor(second):
            assert read_operation(
                second,
                operation="CREATE",
                operation_key="postgres-create",
            ) == created
            restored = read_draft(
                second,
                draft_id="postgres-draft",
                revision=1,
                receipt_digest=created["receipt_digest"],
            )
            assert restored["content_digest"] == created["content_digest"]
            assert second.store.runtime_role_safe() is True
            archive_bytes = export_sealed_draft(
                second, draft_id="postgres-draft", revision=2,
                receipt_digest=sealed["receipt_digest"],
            )
            installed = tmp_path / "exported-after-postgres-restart"
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                archive.extractall(installed)
            exported_runtime = load_enterprise_quote_pilot_pack(installed)
            assert exported_runtime.pack_digest == sealed["validation"]["pack_digest"]
            assert exported_runtime.source_admission.digest == sealed["validation"]["source_admission_receipt_digest"]
    finally:
        second.close()

    with StateStore(
        credentials["migration_dsn"],
        tenant_id=runtime.profile.organization_id,
        migrate=False,
    ) as operator:
        operator.register_workspace(
            "other-workspace",
            profile_digest=runtime.profile.digest,
            pack_digest=runtime.pack_digest,
            quote_object_id=runtime.quote_object_id,
            created_at="2026-09-26T00:00:00Z",
        )
    other = WorkspaceService(**kwargs, workspace_id="other-workspace")
    try:
        with governor(other), pytest.raises(KeyError, match="ARTIFACT_NOT_FOUND"):
            read_operation(
                other,
                operation="CREATE",
                operation_key="postgres-create",
            )
        assert other.store.connection.execute("SELECT count(*) FROM private_records").fetchone()[0] == 0
    finally:
        other.close()
