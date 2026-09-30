from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.onboarding import onboarding_status
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace


def test_onboarding_status_separates_defined_templates_from_runtime_and_customer_qualification(
    workspace,
):
    result = onboarding_status(workspace)

    assert result["schema_version"] == "orgrebase.enterprise-onboarding-status.v1"
    assert len(result["required_inputs"]) == 5
    assert {item["input_class"] for item in result["required_inputs"]} == {
        "BUSINESS_SCOPE",
        "FACTS_AND_RULES",
        "RESPONSIBILITY_AND_PERMISSION",
        "CAPABILITIES",
        "DEPENDENCIES",
    }
    active = next(
        item for item in result["templates"]
        if item["template_ref"] == result["default_task"]["template_ref"]
    )
    assert active["runtime_status"] == "CONFIGURED_RUNTIME_HANDLER"
    assert active["highest_verified_stage"] == "FORMATION_VERIFIED"
    assert active["profile_binding"] == {
        "profile_ref": workspace.profile.ref,
        "profile_digest": workspace.profile.digest,
        "handler_profile": workspace.profile.runtime_compatibility.handler_profile,
    }
    assert active["adapter_binding"]["bound_by_pack_digest"] == workspace.deployment_binding["pack_digest"]
    assert active["adapter_binding"]["adapter_digest"] is None
    assert active["adapter_binding"]["adapter_digest_status"] == "BOUND_BY_PACK_DIGEST_ONLY"
    assert active["output_schema_ref"].startswith("schema:")
    assert active["output_schema_digest"] is None
    assert active["output_schema_digest_status"] == "BOUND_BY_TEMPLATE_DIGEST_ONLY"
    assert active["required_slots"]
    assert active["customer_qualification"] == "NOT_RUN"
    assert all(
        item["runtime_status"] == "CONTRACT_ONLY"
        for item in result["templates"]
        if item["template_ref"] != active["template_ref"]
    )
    stages = {item["stage"]: item["state"] for item in result["stages"]}
    assert stages["PACK_SEALED"] == "COMPLETE"
    assert stages["PROFILE_ADMITTED"] == "COMPLETE"
    assert stages["WORKSPACE_ACTIVATED"] == "COMPLETE"
    assert stages["FORMATION_COMMITTED"] == "COMPLETE"
    assert stages["GOVERNED_CHANGE_VERIFIED"] == "NOT_RUN"
    assert stages["CUSTOMER_QUALIFIED"] == "NOT_RUN"
    assert result["input_preflight_status"] == "RUNTIME_READY"
    assert all(item["status"] == "READY" for item in result["required_inputs"])
    assert all(
        "locator" not in root
        for item in result["required_inputs"]
        for root in item["source_roots"]
    )
    assert result["authority_created_by_projection"] is False
    assert result["canonical_writes"] == result["target_writes"] == 0


def test_onboarding_marks_governed_change_only_after_exact_apply(workspace):
    before = onboarding_status(workspace)
    assert next(
        item for item in before["templates"]
        if item["template_ref"] == before["default_task"]["template_ref"]
    )["governed_change_status"] == "NOT_RUN"

    request = command(workspace, event_id="onboarding-change")
    submit_change(workspace, request)
    preview = workspace.preview_change(request.event_id)
    approval = workspace.approve_change(
        request.event_id,
        actor_id=workspace.change_owner[request.event_id],
        preview_digest=preview.preview.digest,
    )
    workspace.apply_approved_change(
        request.event_id,
        approval_digest=approval["approval_digest"],
    )

    after = onboarding_status(workspace)
    active = next(
        item for item in after["templates"]
        if item["template_ref"] == after["default_task"]["template_ref"]
    )
    assert active["governed_change_status"] == "VERIFIED"
    assert active["highest_verified_stage"] == "GOVERNED_CHANGE_VERIFIED"
    stages = {item["stage"]: item["state"] for item in after["stages"]}
    assert stages["GOVERNED_CHANGE_VERIFIED"] == "COMPLETE"
    assert stages["CUSTOMER_QUALIFIED"] == "NOT_RUN"


def test_onboarding_route_is_read_only_and_does_not_expose_private_values(workspace):
    before = workspace.store.audit_head()
    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/onboarding-status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile_digest"] == workspace.profile.digest
    assert "token" not in str(payload).lower()
    assert workspace.store.audit_head() == before


def test_dual_profile_catalog_reports_only_exact_configured_templates(tmp_path):
    from tests.workspace.test_deliverable_set import make_dual_service

    service, _, profile = make_dual_service(tmp_path)
    try:
        before = service.store.audit_head()
        result = onboarding_status(service)
        configured = [item for item in result["templates"] if item["runtime_status"] == "CONFIGURED_RUNTIME_HANDLER"]
        assert {item["template_ref"] for item in configured} == {item.template_ref for item in profile.members}
        assert all(item["deliverable_set_binding"]["profile_digest"] == profile.digest for item in configured)
        assert all(item["formation_status"] == "NOT_RUN" for item in configured)
        assert all(item["customer_qualification"] == "NOT_RUN" for item in configured)
        assert service.store.audit_head() == before
    finally:
        service.close()
