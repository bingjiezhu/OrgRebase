from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError, request_authorization
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.service import CONTROLLED_LOCAL_HEADER_IDENTITY, WorkspaceService
from orgrebase.workspace.skill_packages import SkillPackageRegistry
from orgrebase.workspace.skill_revision import (
    SKILL_DRAFT_MEDIA_TYPE,
    create_skill_revision_draft,
    skill_source_catalog,
)

ROOT = Path(__file__).resolve().parents[2]


def _revised_quote_source(registry: SkillPackageRegistry) -> tuple[object, str]:
    package = registry.load("enterprise-quote-compose")
    source = package.skill_bytes.decode("utf-8")
    return package, source.replace("version: 1.3.1", "version: 1.3.2", 1)


def test_skill_draft_session_revocation_at_commit_rolls_back_all_writes(tmp_path):
    registry = SkillPackageRegistry(ROOT)
    package, content = _revised_quote_source(registry)
    checks = []

    def authorization():
        checks.append(True)
        if len(checks) == 2:
            raise AuthenticationError("AUTH_LOCAL_SESSION_REQUIRED")

    with StateStore(tmp_path / "revoked.sqlite") as store:
        before = tuple(store.connection.iterdump())
        token = request_authorization.set(authorization)
        try:
            with pytest.raises(AuthenticationError, match="AUTH_LOCAL_SESSION_REQUIRED"):
                create_skill_revision_draft(store, name=package.name, actor_id="human:skill-steward",
                    source_package_digest=package.package_digest, source_skill_digest=package.resource_digests["skill"],
                    proposed_version="1.3.2", content=content, registry=registry)
        finally:
            request_authorization.reset(token)
        assert len(checks) == 2
        assert tuple(store.connection.iterdump()) == before


def test_catalog_returns_exact_verified_sources_and_persisted_drafts(
    tmp_path: Path,
) -> None:
    registry = SkillPackageRegistry(ROOT)
    with StateStore(tmp_path / "skill-drafts.sqlite3") as store:
        initial = skill_source_catalog(store, registry=registry)
        assert initial["status"] == "PASS"
        assert initial["published_source_writes"] == 0
        assert len(initial["packages"]) == 3
        quote = next(
            item for item in initial["packages"] if item["name"] == "enterprise-quote-compose"
        )
        package = registry.load("enterprise-quote-compose")
        assert quote["content"].encode("utf-8") == package.skill_bytes
        assert quote["skill_digest"] == package.resource_digests["skill"]
        assert quote["release_state"] == "PUBLISHED_IMMUTABLE"
        assert quote["editable"] is False
        assert quote["drafts"] == []

        _, content = _revised_quote_source(registry)
        receipt = create_skill_revision_draft(
            store,
            name=package.name,
            actor_id="human:skill-steward",
            source_package_digest=package.package_digest,
            source_skill_digest=package.resource_digests["skill"],
            proposed_version="1.3.2",
            content=content,
            registry=registry,
        )
        assert receipt["status"] == "DRAFT_SAVED"
        assert receipt["evaluation_status"] == "NOT_EVALUATED"
        assert receipt["release_status"] == "NOT_RELEASED"
        assert receipt["executable"] is False
        assert receipt["registry_writes"] == 0
        assert receipt["idempotent_replay"] is False

        repeated = create_skill_revision_draft(
            store,
            name=package.name,
            actor_id="human:skill-steward",
            source_package_digest=package.package_digest,
            source_skill_digest=package.resource_digests["skill"],
            proposed_version="1.3.2",
            content=content,
            registry=registry,
        )
        assert repeated["artifact_id"] == receipt["artifact_id"]
        assert repeated["idempotent_replay"] is True
        assert [event["event_type"] for event in store.event_records()] == [
            "SKILL_REVISION_DRAFT_CREATED"
        ]
        artifact = store.load_artifact(
            receipt["artifact_id"],
            expected_media_type=SKILL_DRAFT_MEDIA_TYPE,
        )
        assert artifact.payload["content"] == content

        refreshed = skill_source_catalog(store, registry=registry)
        quote_after = next(
            item
            for item in refreshed["packages"]
            if item["name"] == "enterprise-quote-compose"
        )
        assert len(quote_after["drafts"]) == 1
        assert quote_after["drafts"][0]["artifact_id"] == receipt["artifact_id"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("actor_id", "human:finance-owner", "SKILL_DRAFT_ACTOR_DENIED"),
        ("source_package_digest", "sha256:" + "0" * 64, "SKILL_PACKAGE_MANIFEST_DIGEST_MISMATCH"),
        ("source_skill_digest", "sha256:" + "0" * 64, "SKILL_DRAFT_SOURCE_DIGEST_MISMATCH"),
        ("proposed_version", "1.3.1", "SKILL_DRAFT_VERSION_NOT_ADVANCED"),
        ("proposed_version", "1.3.0", "SKILL_DRAFT_VERSION_NOT_ADVANCED"),
        ("proposed_version", "1.2.9", "SKILL_DRAFT_VERSION_NOT_ADVANCED"),
        ("proposed_version", "1.3.1-rc.1", "SKILL_DRAFT_VERSION_NOT_ADVANCED"),
    ],
)
def test_draft_fails_closed_without_mutating_store(
    tmp_path: Path,
    field: str,
    value: str,
    error: str,
) -> None:
    registry = SkillPackageRegistry(ROOT)
    package, content = _revised_quote_source(registry)
    arguments = {
        "name": package.name,
        "actor_id": "human:skill-steward",
        "source_package_digest": package.package_digest,
        "source_skill_digest": package.resource_digests["skill"],
        "proposed_version": "1.3.2",
        "content": content,
        "registry": registry,
    }
    arguments[field] = value
    with StateStore(tmp_path / f"{field}.sqlite3") as store:
        expected = AuthorizationError if field == "actor_id" else IntegrityError
        with pytest.raises(expected, match=error):
            create_skill_revision_draft(store, **arguments)
        assert store.list_artifacts(
            artifact_id_prefix="skill-revision-draft:",
            expected_media_type=SKILL_DRAFT_MEDIA_TYPE,
        ) == ()
        assert store.event_records() == ()


def test_skill_source_api_uses_header_identity_and_does_not_change_runtime_skill(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite3",
        approval_identity_mode=CONTROLLED_LOCAL_HEADER_IDENTITY,
    )
    try:
        with TestClient(create_app(workspace_service=workspace)) as client:
            catalog = client.get("/api/workspace/skills")
            assert catalog.status_code == 200
            quote = next(
                item
                for item in catalog.json()["packages"]
                if item["name"] == "enterprise-quote-compose"
            )
            content = quote["content"].replace("version: 1.3.1", "version: 1.3.2", 1)
            payload = {
                "actor_id": "human:skill-steward",
                "source_package_digest": quote["package_digest"],
                "source_skill_digest": quote["skill_digest"],
                "proposed_version": "1.3.2",
                "content": content,
            }
            missing = client.post(
                "/api/workspace/skills/enterprise-quote-compose/drafts",
                json=payload,
            )
            assert missing.status_code == 403
            assert missing.json()["detail"]["code"] == "WORKSPACE_ACTOR_HEADER_REQUIRED"

            wrong = client.post(
                "/api/workspace/skills/enterprise-quote-compose/drafts",
                json=payload,
                headers={"X-OrgRebase-Actor": "human:finance-owner"},
            )
            assert wrong.status_code == 403
            assert wrong.json()["detail"]["code"] == "AUTHZ_DENIED"

            saved = client.post(
                "/api/workspace/skills/enterprise-quote-compose/drafts",
                json={**payload, "actor_id": "human:finance-owner"},
                headers={"X-OrgRebase-Actor": "human:skill-steward"},
            )
            assert saved.status_code == 200
            assert saved.json()["status"] == "DRAFT_SAVED"
            assert saved.json()["registry_writes"] == 0

            refreshed = client.get("/api/workspace/skills").json()
            quote_after = next(
                item
                for item in refreshed["packages"]
                if item["name"] == "enterprise-quote-compose"
            )
            assert len(quote_after["drafts"]) == 1
            assert quote_after["package_digest"] == quote["package_digest"]
            assert quote_after["skill_digest"] == quote["skill_digest"]
    finally:
        workspace.close()
