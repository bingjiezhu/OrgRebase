from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.local_role_session import LocalRoleSessions, LocalRoleSessionSettings
from orgrebase.runtime_config import (
    QUOTE_DISCOUNT_MEMO_PROFILE,
    DeploymentSettings,
    open_workspace,
)
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from tests.workspace.test_deliverable_set import make_dual_service, propose
from tests.workspace.test_priced_quote_pack import BASKET, POLICY, priced_draft


def test_http_basket_quantity_change_preserves_reviewed_source_through_both_exports(
    tmp_path: Path,
) -> None:
    service, _, profile = make_dual_service(tmp_path, name="http-basket-source")
    try:
        service.form_quote()
        predecessor = service.current_quote()
        with TestClient(create_app(workspace_service=service)) as client:
            options = client.get("/api/workspace/change-options")
            assert options.status_code == 200, options.text
            field = next(item for item in options.json()["fields"] if item["slot_id"] == "quote_basket")
            source_ref = "source:customer-reviewed-basket@v2"
            basket = {**BASKET, "items": [{**BASKET["items"][0], "quantity": 4}], "source_ref": source_ref}
            proposed = client.post("/api/workspace/change-proposals", json={
                "event_id": "basket-http", "slot_id": "quote_basket",
                "base_version": field["current"]["version"], "base_digest": field["current"]["digest"],
                "value": basket, "source_ref": source_ref, "reason": "Customer requested four units",
            })
            assert proposed.status_code == 200, proposed.text
            assert service.current_quote().digest == predecessor.digest
            waiting_for_preview = client.get("/api/workspace/deliverable-set/changes/basket-http")
            assert waiting_for_preview.status_code == 409, waiting_for_preview.text
            assert waiting_for_preview.json()["detail"]["code"] == "WORKSPACE_PREVIEW_REQUIRED"
            current_before_preview = client.get("/api/workspace/export/quote")
            assert current_before_preview.status_code == 200, current_before_preview.text
            assert len(current_before_preview.json()["deliverables"]) == 2
            assert current_before_preview.json()["quote"]["digest"] == predecessor.digest
            preview = client.post("/api/workspace/preview/basket-http")
            assert preview.status_code == 200, preview.text
            preview_digest = preview.json()["preview_digest"]
            with service._test_as_actor(service.change_owner["basket-http"]):
                source_approval = client.post("/api/workspace/approve/basket-http", json={
                    "actor_id": service.change_owner["basket-http"], "preview_digest": preview_digest,
                })
            assert source_approval.status_code == 200, source_approval.text
            for member in profile.members:
                with service._test_as_actor(member.owner_id):
                    decision = client.post("/api/workspace/approve/basket-http/deliverable-set", json={
                        "operation_id": "basket-source-review", "preview_digest": preview_digest, "decision": "APPROVED",
                    })
                assert decision.status_code == 200, decision.text
            with service._test_as_actor("executor:one"):
                applied = client.post("/api/workspace/apply/basket-http", json={
                    "approval_digest": source_approval.json()["approval_digest"],
                })
            assert applied.status_code == 200, applied.text
            exported = client.get("/api/workspace/export/deliverable-set")
            assert exported.status_code == 200, exported.text
            for member in exported.json()["deliverables"]:
                pricing = member["payload"]["pricing"]
                assert pricing["basket_source_ref"] == source_ref
                assert pricing["total"] == "57.00"
            quote_export = client.get("/api/workspace/export/quote")
            assert quote_export.status_code == 200, quote_export.text
            assert quote_export.json()["quote"]["payload"]["pricing"]["basket_source_ref"] == source_ref
            assert predecessor.payload["pricing"]["basket_source_ref"] == BASKET["source_ref"]
            assert predecessor.payload["pricing"]["lines"][0]["quantity"] == 3
    finally:
        service.close()


def test_http_current_principals_complete_and_read_dual_deliverable_approvals(
    tmp_path: Path,
) -> None:
    service, _, profile = make_dual_service(tmp_path, name="http-dual")
    try:
        service.form_quote()
        bundle = propose(
            service,
            "discount-http",
            "pricing_policy",
            {**POLICY, "discount_bps": 1000, "source_ref": "source:controlled:discount-http@v1"},
        )
        owners = tuple(item.owner_id for item in profile.members)
        with service._test_as_actor(owners[0]):
            before_source_approval = service.deliverable_set_change_view(
                "discount-http"
            )
        assert before_source_approval["allowed_actions"] == []
        with service._test_as_actor(service.change_owner["discount-http"]):
            source = service.approve_change(
                "discount-http",
                actor_id=service.change_owner["discount-http"],
                preview_digest=bundle.preview.digest,
            )
        with TestClient(create_app(workspace_service=service)) as client:
            current = client.get("/api/workspace/deliverable-set")
            assert current.status_code == 200
            assert {item["deliverable_kind"] for item in current.json()["members"]} == {
                "QUOTE",
                "DISCOUNT_MEMO",
            }
            with service._test_as_actor("executor:one"):
                waiting = client.get("/api/workspace/changes/discount-http")
            assert waiting.status_code == 200
            assert "APPLY" not in waiting.json()["allowed_actions"]
            assert waiting.json()["deliverable_approval_required"] is True
            decisions = []
            for owner in owners:
                with service._test_as_actor(owner):
                    response = client.post(
                        "/api/workspace/approve/discount-http/deliverable-set",
                        json={
                            "operation_id": "shared-http-decision",
                            "preview_digest": bundle.preview.digest,
                            "decision": "APPROVED",
                        },
                    )
                assert response.status_code == 200, response.text
                decisions.append(response.json())
            assert decisions[0]["approval_set"]["status"] == "INCOMPLETE"
            assert decisions[1]["approval_set"]["status"] == "COMPLETE"
            with service._test_as_actor(owners[1]):
                repeated = client.post(
                    "/api/workspace/approve/discount-http/deliverable-set",
                    json={
                        "operation_id": "shared-http-decision",
                        "preview_digest": bundle.preview.digest,
                        "decision": "APPROVED",
                    },
                )
            assert repeated.status_code == 200
            assert repeated.json()["decision_digest"] == decisions[1]["decision_digest"]
            with service._test_as_actor(owners[0]):
                conflict = client.post(
                    "/api/workspace/approve/discount-http/deliverable-set",
                    json={
                        "operation_id": "shared-http-decision",
                        "preview_digest": bundle.preview.digest,
                        "decision": "REJECTED",
                    },
                )
            assert conflict.status_code == 409
            change_view = client.get(
                "/api/workspace/deliverable-set/changes/discount-http"
            )
            assert change_view.status_code == 200
            assert change_view.json()["approval_set"]["status"] == "COMPLETE"
            assert change_view.json()["pending_owner_ids"] == []
            with service._test_as_actor("executor:one"):
                ready = client.get("/api/workspace/changes/discount-http")
                assert "APPLY" in ready.json()["allowed_actions"]
                assert ready.json()["deliverable_approval_required"] is False
                applied = client.post(
                    "/api/workspace/apply/discount-http",
                    json={"approval_digest": source["approval_digest"]},
                )
            assert applied.status_code == 200, applied.text
            with service._test_as_actor(owners[1]):
                after_apply_retry = client.post(
                    "/api/workspace/approve/discount-http/deliverable-set",
                    json={
                        "operation_id": "shared-http-decision",
                        "preview_digest": bundle.preview.digest,
                        "decision": "APPROVED",
                    },
                )
            assert after_apply_retry.status_code == 200
            assert (
                after_apply_retry.json()["decision_digest"]
                == decisions[1]["decision_digest"]
            )
            exported = client.get("/api/workspace/export/deliverable-set")
            assert exported.status_code == 200
            assert exported.headers["content-disposition"].endswith(
                '"orgrebase-deliverable-set.json"'
            )
            payload = exported.json()
            assert {item["payload"]["deliverable_kind"] for item in payload["deliverables"]} == {
                "QUOTE",
                "DISCOUNT_MEMO",
            }
            assert payload["changes"]["discount-http"]["outcome"] is not None
            quote_export = client.get("/api/workspace/export/quote")
            evidence_export = client.get("/api/workspace/export/evidence")
            assert quote_export.status_code == evidence_export.status_code == 200
            assert len(quote_export.json()["deliverables"]) == 2
            assert evidence_export.json()["deliverable_set"]["changes"][
                "discount-http"
            ]["approval_set"]["status"] == "COMPLETE"
    finally:
        service.close()


def test_standard_workspace_factory_selects_only_the_fixed_dual_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED", "0")
    draft = priced_draft(tmp_path / "source")
    pack = tmp_path / "pack"
    seal_enterprise_quote_pilot_pack(draft, pack)
    settings = DeploymentSettings(
        mode="local",
        database_url=str(tmp_path / "dual.sqlite"),
        enterprise_pack=str(pack),
        deliverable_profile=QUOTE_DISCOUNT_MEMO_PROFILE,
    )
    workspace = open_workspace(settings)
    try:
        assert workspace.deliverable_set_profile is not None
        assert workspace.deliverable_set_profile.id == "profile:quote-discount-memo"
        roles = LocalRoleSessions(
            LocalRoleSessionSettings("http://127.0.0.1:8791"),
            lambda: workspace,
            skill_steward_actor="human:skill-steward",
        ).actors()
        quote_owner = next(
            item.owner_id
            for item in workspace.deliverable_set_profile.members
            if item.deliverable_kind == "QUOTE"
        )
        quote_actor = next(item for item in roles if item["actor_id"] == quote_owner)
        assert {"operator", "approver"} <= set(quote_actor["roles"])
        workspace.form_quote()
        assert {item.payload["deliverable_kind"] for item in workspace.current_deliverables()} == {
            "QUOTE",
            "DISCOUNT_MEMO",
        }
    finally:
        workspace.close()

    with pytest.raises(ValueError, match="DELIVERABLE_PROFILE_INVALID"):
        DeploymentSettings(mode="local", deliverable_profile="arbitrary-python-handler")

    monkeypatch.setenv("ORGREBASE_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("ORGREBASE_ENTERPRISE_PACK", str(pack))
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", str(tmp_path / "dual-env.sqlite"))
    monkeypatch.setenv("ORGREBASE_DELIVERABLE_PROFILE", QUOTE_DISCOUNT_MEMO_PROFILE)
    configured = DeploymentSettings.from_environment()
    assert configured.enterprise_pack == str(pack)
    assert configured.deliverable_profile == QUOTE_DISCOUNT_MEMO_PROFILE
    reopened = open_workspace(configured)
    try:
        assert reopened.deliverable_set_profile is not None
    finally:
        reopened.close()
