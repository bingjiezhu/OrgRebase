from __future__ import annotations

import io
import json
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.local_role_session import LOCAL_SESSION_COOKIE, LocalRoleSessionSettings
from orgrebase.resource_paths import runtime_asset_path
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.workspace import pilot_authoring
from orgrebase.workspace.onboarding_drafts import (
    CreateOnboardingDraft,
    EnterpriseOnboardingDraftError,
    PreflightOnboardingDraft,
    SealOnboardingDraft,
    UpdateOnboardingDraft,
    create_draft,
    export_sealed_draft,
    preflight_draft,
    read_draft,
    read_operation,
    seal_draft,
    update_draft,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

ORIGIN = "http://127.0.0.1:8791"


class MutableClock:
    value = "2026-09-26T00:00:00Z"

    def now(self) -> str:
        return self.value


def draft_files() -> dict[str, dict]:
    root = runtime_asset_path("examples/enterprise-quote-pilot/evergreen")
    return {
        path.relative_to(root).as_posix(): json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("*.json"))
    }


@contextmanager
def governor(workspace: WorkspaceService, *, subject: str | None = None):
    actor_id = workspace.profile.governance.admission_authority_refs[0]
    token = request_principal.set(
        Principal(
            issuer="https://identity.example.test",
            subject=subject or actor_id,
            tenant_id=workspace.profile.organization_id,
            actor_id=actor_id,
            roles=frozenset({"governor"}),
            expires_at=2**63 - 1,
        )
    )
    try:
        yield actor_id
    finally:
        request_principal.reset(token)


def test_immutable_draft_operations_are_private_idempotent_and_recoverable(tmp_path: Path) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "workspace.sqlite")
    try:
        files = draft_files()
        files["pack.json"]["scenario"]["label"] = "private-onboarding-canary"
        with governor(workspace):
            created = create_draft(
                workspace,
                CreateOnboardingDraft(
                    operation_key="create-001",
                    draft_id="enterprise-pilot",
                    files=files,
                ),
            )
            assert created["status"] == "DRAFT_CREATED"
            assert created["revision"] == 1
            assert created["private_input_status"] == "AVAILABLE"

            changed = json.loads(json.dumps(files))
            changed["pack.json"]["scenario"]["label"] = "Private operator canary"
            updated = update_draft(
                workspace,
                UpdateOnboardingDraft(
                    operation_key="update-001",
                    draft_id="enterprise-pilot",
                    base_revision=1,
                    base_receipt_digest=created["receipt_digest"],
                    files=changed,
                ),
            )
            assert updated["status"] == "DRAFT_UPDATED"
            assert updated["revision"] == 2
            assert updated["parent_receipt_digest"] == created["receipt_digest"]

            preflight = preflight_draft(
                workspace,
                PreflightOnboardingDraft(
                    operation_key="preflight-001",
                    draft_id="enterprise-pilot",
                    revision=2,
                    draft_receipt_digest=updated["receipt_digest"],
                ),
            )
            assert preflight["status"] == "DRAFT_PREFLIGHT_PASSED"
            assert preflight["validation"]["workspace_activated"] is False
            assert len(preflight["validation"]["required_inputs"]) == 5

            sealed = seal_draft(
                workspace,
                SealOnboardingDraft(
                    operation_key="seal-001",
                    draft_id="enterprise-pilot",
                    revision=2,
                    draft_receipt_digest=updated["receipt_digest"],
                ),
            )
            assert sealed["status"] == "DRAFT_SEALED"
            assert sealed["revision"] == 3
            assert sealed["workspace_activated"] is False
            assert sealed["profile_admitted_for_workspace"] is False
            assert sealed["canonical_target_writes"] == 0

            assert seal_draft(
                workspace,
                SealOnboardingDraft(
                    operation_key="seal-001",
                    draft_id="enterprise-pilot",
                    revision=2,
                    draft_receipt_digest=updated["receipt_digest"],
                ),
            ) == sealed
            assert read_operation(
                workspace,
                operation="SEAL",
                operation_key="seal-001",
            ) == sealed
            restored = read_draft(
                workspace,
                draft_id="enterprise-pilot",
                revision=3,
                receipt_digest=sealed["receipt_digest"],
            )
            assert restored["files"]["pack.json"]["scenario"]["label"] == (
                "Private operator canary"
            )

            conflicting = json.loads(json.dumps(files))
            conflicting["pack.json"]["scenario"]["label"] = "different private value"
            with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
                create_draft(
                    workspace,
                    CreateOnboardingDraft(
                        operation_key="create-001",
                        draft_id="enterprise-pilot",
                        files=conflicting,
                    ),
                )
            with pytest.raises(
                EnterpriseOnboardingDraftError,
                match="ONBOARDING_DRAFT_REVISION_CONFLICT",
            ):
                update_draft(
                    workspace,
                    UpdateOnboardingDraft(
                        operation_key="competing-update",
                        draft_id="enterprise-pilot",
                        base_revision=1,
                        base_receipt_digest=created["receipt_digest"],
                        files=conflicting,
                    ),
                )

        canonical = "".join(
            row[0]
            for row in workspace.store.connection.execute(
                "SELECT payload_json FROM artifacts UNION ALL "
                "SELECT result_json FROM idempotency_records"
            ).fetchall()
        )
        assert "private-onboarding-canary" not in canonical
        assert "Private operator canary" not in canonical
        assert '"files":' not in canonical
        private_rows = "".join(
            row[0] or ""
            for row in workspace.store.connection.execute(
                "SELECT content_json FROM private_records"
            ).fetchall()
        )
        assert "private-onboarding-canary" in private_rows
        assert "Private operator canary" in private_rows
        assert not any("APPROV" in event["event_type"] for event in workspace.store.event_envelopes())
    finally:
        workspace.close()


def test_private_preflight_and_seal_never_materialize_operating_system_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = draft_files()
    files["pack.json"]["scenario"]["label"] = "memory-only-private-canary"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("PRIVATE_DRAFT_FILESYSTEM_MATERIALIZATION_FORBIDDEN")

    monkeypatch.setattr(pilot_authoring.tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    workspace = WorkspaceService(store_path=tmp_path / "workspace.sqlite")
    try:
        with governor(workspace):
            created = create_draft(
                workspace,
                CreateOnboardingDraft(
                    operation_key="memory-create",
                    draft_id="memory-draft",
                    files=files,
                ),
            )
            preflight = preflight_draft(
                workspace,
                PreflightOnboardingDraft(
                    operation_key="memory-preflight",
                    draft_id="memory-draft",
                    revision=1,
                    draft_receipt_digest=created["receipt_digest"],
                ),
            )
            sealed = seal_draft(
                workspace,
                SealOnboardingDraft(
                    operation_key="memory-seal",
                    draft_id="memory-draft",
                    revision=1,
                    draft_receipt_digest=created["receipt_digest"],
                ),
            )
        assert preflight["status"] == "DRAFT_PREFLIGHT_PASSED"
        assert sealed["status"] == "DRAFT_SEALED"
        canonical = "".join(
            row[0]
            for row in workspace.store.connection.execute(
                "SELECT payload_json FROM artifacts UNION ALL "
                "SELECT result_json FROM idempotency_records"
            ).fetchall()
        )
        assert "memory-only-private-canary" not in canonical
    finally:
        workspace.close()


def test_expiry_denies_resume_then_cleanup_preserves_safe_operation_receipt(tmp_path: Path) -> None:
    clock = MutableClock()
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite",
        clock=clock,
        private_retention_seconds=60,
    )
    try:
        with governor(workspace):
            created = create_draft(
                workspace,
                CreateOnboardingDraft(
                    operation_key="expiry-create",
                    draft_id="expiring-draft",
                    files=draft_files(),
                ),
            )
            assert read_draft(
                workspace,
                draft_id="expiring-draft",
                revision=1,
                receipt_digest=created["receipt_digest"],
            )["private_input_status"] == "AVAILABLE"
            clock.value = "2026-09-26T00:01:00.000001Z"
            with pytest.raises(
                EnterpriseOnboardingDraftError,
                match="ONBOARDING_DRAFT_PRIVATE_INPUT_UNAVAILABLE",
            ):
                read_draft(
                    workspace,
                    draft_id="expiring-draft",
                    revision=1,
                    receipt_digest=created["receipt_digest"],
                )
            assert read_operation(
                workspace,
                operation="CREATE",
                operation_key="expiry-create",
            )["private_input_status"] == "EXPIRED"
            assert workspace.private_records.purge_expired() == 1
            assert read_operation(
                workspace,
                operation="CREATE",
                operation_key="expiry-create",
            )["private_input_status"] == "DELETED"
        assert "profile.json" not in json.dumps(workspace.private_records.deletion_ledger())
        assert workspace.store.verify_event_chain()["status"] == "PASS"
    finally:
        workspace.close()


def test_invalid_private_draft_records_one_safe_rejection_for_operation_recovery(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "workspace.sqlite")
    try:
        files = draft_files()
        files["pack.json"]["scenario"]["primary_user"] = "unsupported private user"
        with governor(workspace):
            created = create_draft(
                workspace,
                CreateOnboardingDraft(
                    operation_key="invalid-create",
                    draft_id="invalid-draft",
                    files=files,
                ),
            )
            command = PreflightOnboardingDraft(
                operation_key="invalid-preflight",
                draft_id="invalid-draft",
                revision=1,
                draft_receipt_digest=created["receipt_digest"],
            )
            rejected = preflight_draft(workspace, command)
            assert rejected["status"] == "DRAFT_PREFLIGHT_REJECTED"
            assert rejected["reason_code"] == "PILOT_AUTHOR_PACK_SCHEMA_INVALID"
            assert rejected["validation"] is None
            assert preflight_draft(workspace, command) == rejected
            assert read_operation(
                workspace,
                operation="PREFLIGHT",
                operation_key="invalid-preflight",
            ) == rejected
        canonical = "".join(
            row[0]
            for row in workspace.store.connection.execute(
                "SELECT payload_json FROM artifacts UNION ALL "
                "SELECT result_json FROM idempotency_records"
            ).fetchall()
        )
        assert "unsupported private user" not in canonical
    finally:
        workspace.close()


def csrf(session: dict) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    if session["csrf_token"]:
        headers["X-CSRF-Token"] = session["csrf_token"]
    else:
        headers["X-OrgRebase-Local-Session"] = "initialize"
    return headers


def choose(client: TestClient, actor_id: str) -> dict:
    current = client.get("/api/session").json()
    response = client.post(
        "/api/session/local-actor",
        json={"actor_id": actor_id},
        headers=csrf(current),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_http_session_switch_rejects_foreign_view_and_same_principal_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = load_enterprise_quote_pilot_pack(
        runtime_asset_path("examples/enterprise-quote-pilot/evergreen")
    )
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite",
        runtime_configuration=runtime,
    )
    settings = DeploymentSettings(
        mode="local",
        local_role_session=LocalRoleSessionSettings(ORIGIN),
    )
    app = create_app(workspace_service=workspace, deployment_settings=settings)
    try:
        with TestClient(
            app,
            base_url=ORIGIN,
            client=("127.0.0.1", 42000),
        ) as client:
            anonymous = client.get("/api/session").json()
            owner = next(
                actor["actor_id"]
                for actor in anonymous["actors"]
                if "governor" in actor["roles"]
            )
            other = next(
                actor["actor_id"]
                for actor in anonymous["actors"]
                if "governor" in actor["roles"] and actor["actor_id"] != owner
            )
            owner_session = choose(client, owner)
            owner_cookie = client.cookies.get(LOCAL_SESSION_COOKIE)
            created_response = client.post(
                "/api/workspace/onboarding-drafts",
                json={
                    "operation_key": "browser-create",
                    "draft_id": "browser-draft",
                    "files": draft_files(),
                },
                headers=csrf(owner_session),
            )
            assert created_response.status_code == 200, created_response.text
            assert created_response.headers["cache-control"] == "no-store"
            created = created_response.json()

            other_session = choose(client, other)
            with pytest.raises(AuthenticationError):
                app.state.local_role_sessions.authenticate(owner_cookie)
            denied = client.get(
                "/api/workspace/onboarding-draft-operations/CREATE/browser-create"
            )
            assert denied.status_code == 403
            assert denied.json()["detail"]["code"] == "ONBOARDING_DRAFT_OWNER_DENIED"

            restored_session = choose(client, owner)
            recovered = client.get(
                "/api/workspace/onboarding-draft-operations/CREATE/browser-create"
            )
            assert recovered.status_code == 200
            assert recovered.json() == created
            private_view = client.get(
                "/api/workspace/onboarding-drafts/browser-draft/revisions/1",
                params={"receipt_digest": created["receipt_digest"]},
            )
            assert private_view.status_code == 200
            assert private_view.headers["cache-control"] == "no-store"
            assert set(private_view.json()["files"]) == set(draft_files())

            invalid = client.post(
                "/api/workspace/onboarding-drafts",
                json={"operation_key": "invalid", "draft_id": "private-invalid",
                      "files": {"pack.json": {"private": "PRIVATE_ERROR_SENTINEL"}}},
                headers=csrf(restored_session),
            )
            assert invalid.status_code == 422
            assert invalid.json() == {"detail": {"code": "ONBOARDING_DRAFT_REQUEST_INVALID"}}
            assert "PRIVATE_ERROR_SENTINEL" not in invalid.text
            assert invalid.headers["cache-control"] == "no-store"

            sealed = client.post(
                "/api/workspace/onboarding-drafts/seal",
                json={"operation_key": "browser-seal", "draft_id": "browser-draft",
                      "revision": 1, "draft_receipt_digest": created["receipt_digest"]},
                headers=csrf(restored_session),
            ).json()
            exported = client.get(
                "/api/workspace/onboarding-drafts/browser-draft/revisions/2/export",
                params={"receipt_digest": sealed["receipt_digest"]},
            )
            assert exported.status_code == 200, exported.text
            assert exported.headers["content-type"] == "application/zip"
            assert exported.headers["cache-control"] == "no-store"
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                assert set(archive.namelist()) == set(draft_files())

            original_actor = app.state.local_role_sessions._actor

            def revoked(actor_id: str):
                if actor_id == owner:
                    raise AuthenticationError("AUTH_LOCAL_ACTOR_DENIED", 403)
                return original_actor(actor_id)

            monkeypatch.setattr(app.state.local_role_sessions, "_actor", revoked)
            revoked_response = client.get(
                "/api/workspace/onboarding-draft-operations/CREATE/browser-create"
            )
            assert revoked_response.status_code == 403
            assert revoked_response.json()["detail"]["code"] == "AUTH_LOCAL_ACTOR_DENIED"
            assert restored_session["principal"]["actor_id"] == owner
            assert other_session["principal"]["actor_id"] == other
    finally:
        workspace.close()


@pytest.mark.parametrize("recompute_projection", [False, True])
def test_sealed_draft_export_preserves_installed_identity_and_remains_private(
    tmp_path: Path, recompute_projection: bool,
) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "export.sqlite")
    try:
        with governor(workspace):
            files = draft_files()
            if recompute_projection:
                del files["components/knowledge.json"]["projection_digest"]
            created = create_draft(workspace, CreateOnboardingDraft(
                operation_key="export-create", draft_id="export-draft", files=files,
            ))
            with pytest.raises(EnterpriseOnboardingDraftError, match="SEAL_REQUIRED"):
                export_sealed_draft(workspace, draft_id="export-draft", revision=1,
                                    receipt_digest=created["receipt_digest"])
            sealed = seal_draft(workspace, SealOnboardingDraft(
                operation_key="export-seal", draft_id="export-draft", revision=1,
                draft_receipt_digest=created["receipt_digest"],
            ))
            kwargs = dict(draft_id="export-draft", revision=2, receipt_digest=sealed["receipt_digest"])
            first = export_sealed_draft(workspace, **kwargs)
            assert export_sealed_draft(workspace, **kwargs) == first
        installed = tmp_path / "installed"
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            archive.extractall(installed)
        runtime = load_enterprise_quote_pilot_pack(installed)
        assert runtime.pack_digest == sealed["validation"]["pack_digest"]
        assert runtime.source_admission.digest == sealed["validation"]["source_admission_receipt_digest"]
        assert runtime.runtime_projection.digest == sealed["validation"]["runtime_projection_receipt_digest"]
        # The established filesystem authoring path uses the same compiler.
        receipt = pilot_authoring.seal_enterprise_quote_pilot_pack(installed, tmp_path / "resealed")
        assert receipt["pack_digest"] == runtime.pack_digest
        for relative in draft_files():
            assert (installed / relative).read_bytes() == (tmp_path / "resealed" / relative).read_bytes()
        with governor(workspace, subject="another-author"), pytest.raises(AuthenticationError, match="OWNER_DENIED"):
            export_sealed_draft(workspace, **kwargs)
    finally:
        workspace.close()


def test_sealed_export_rejects_modified_content_instead_of_resealing() -> None:
    files, receipt = pilot_authoring.seal_enterprise_quote_pilot_payloads(draft_files())
    files["components/knowledge.json"]["projection"]["tampered"] = "unapproved"
    with pytest.raises(ValueError):
        pilot_authoring.sealed_enterprise_quote_pilot_bytes(files, expected_pack_digest=receipt["pack_digest"])
