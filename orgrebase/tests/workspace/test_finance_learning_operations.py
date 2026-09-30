from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.domain import IntegrityError
from orgrebase.workspace.finance_learning_operations import (
    bootstrap_finance_skill,
    finance_skill_head,
)
from tests.workspace.test_change_proposals import workspace as workspace


def _governor(workspace) -> Principal:
    return Principal(
        issuer="https://issuer.example", subject="governor",
        tenant_id=workspace.profile.organization_id, actor_id="human:finance-governor",
        roles=frozenset({"administrator"}), expires_at=int(time.time()) + 3600,
    )


def test_static_finance_head_requires_governor_and_remains_unqualified(workspace) -> None:
    with pytest.raises(AuthenticationError, match="FINANCE_SKILL_VERIFIED_PRINCIPAL_REQUIRED"):
        bootstrap_finance_skill(workspace)
    principal_token = request_principal.set(_governor(workspace))
    try:
        created = bootstrap_finance_skill(workspace)
        assert created["generation"] == 0
        assert created["qualification_status"] == "UNQUALIFIED"
        assert created["adoption_enabled"] is False
        assert created["consumer_contract"] == "workspace-change-advisory@4.0.0"
        assert len(created["resource_digests"]) == 2
        assert finance_skill_head(workspace) == created
        assert bootstrap_finance_skill(workspace) == created
        assert workspace.store.verify_event_chain()["status"] == "PASS"
    finally:
        request_principal.reset(principal_token)

    other = Principal(
        issuer="https://issuer.example", subject="other-governor",
        tenant_id=workspace.profile.organization_id, actor_id="human:other-governor",
        roles=frozenset({"administrator"}), expires_at=int(time.time()) + 3600,
    )
    other_token = request_principal.set(other)
    try:
        with pytest.raises(IntegrityError, match="CURRENT_POINTER_ALREADY_EXISTS"):
            bootstrap_finance_skill(workspace)
    finally:
        request_principal.reset(other_token)


def test_finance_head_routes_preserve_read_and_bootstrap_authority(workspace) -> None:
    with TestClient(create_app(workspace_service=workspace)) as client:
        path = "/api/workspace/skills/finance-change-explanation"
        assert client.get(path + "/head").status_code == 409
        assert client.post(path + "/bootstrap").status_code in {401, 403}
        token = request_principal.set(_governor(workspace))
        try:
            response = client.post(path + "/bootstrap")
            assert response.status_code == 200, response.text
            assert response.json()["qualification_status"] == "UNQUALIFIED"
            assert client.get(path + "/head").json()["head_ref"] == response.json()["head_ref"]
            replay = client.post(path + "/bootstrap")
            assert replay.status_code == 200 and replay.json() == response.json()
        finally:
            request_principal.reset(token)
