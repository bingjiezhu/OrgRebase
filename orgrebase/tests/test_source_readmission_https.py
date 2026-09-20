from __future__ import annotations

import json
import ssl
import time
from contextlib import ExitStack

import httpx2 as httpx
import pytest
from test_browser_sessions import csrf_headers, login

pytest_plugins = ("test_browser_sessions",)


@pytest.mark.parametrize("decision", ["apply", "reject"])
def test_source_group_https_bff_separates_operator_owners_and_executor(browser_deployment, decision):
    application, provider, original = browser_deployment
    workspace = application.application.state.workspace_service
    bindings = {item.slot_id: item for item in workspace.enterprise_binding.resources}
    owners = {"product-owner": bindings["launch_date"].owner_id, "finance-owner": bindings["currency"].owner_id}
    members = [{"subject": "operator", "actor_id": workspace.profile.default_task.actor_id, "roles": ["operator"]},
               {"subject": "executor", "actor_id": "human:group-executor", "roles": ["executor"]},
               *[{"subject": subject, "actor_id": actor, "roles": ["approver"]} for subject, actor in owners.items()]]
    application.settings.identity.membership_file.write_text(json.dumps({
        "tenant_id": workspace.profile.organization_id, "members": members}))
    original.close()
    origin = application.settings.browser_session.public_origin
    with ExitStack() as stack:
        clients, headers = {}, {}
        for member in members:
            subject = member["subject"]
            client = stack.enter_context(httpx.Client(base_url=origin, trust_env=False, timeout=20,
                verify=ssl.create_default_context(cafile=str(application.certificate))))
            provider.subject = subject
            session = login(client)
            assert session["principal"]["roles"] == member["roles"]
            clients[subject], headers[subject] = client, csrf_headers(application, session)

        def post(subject, path, body=None, status=200):
            response = clients[subject].post(path, headers=headers[subject], json=body)
            assert response.status_code == status, response.text
            return response.json()

        post("operator", "/api/workspace/form")
        predecessor = workspace.current_quote()
        refs = []
        for slot in ("launch_date", "currency"):
            # The fixture supplies a source outage; proposals and all decisions
            # still traverse the actual authenticated HTTPS application.
            workspace.invalidate_source(slot, f"https://source.example/{slot}", "SOURCE_FIELD_MISSING")
            current = workspace.store.get_object(bindings[slot].object_id)
            proposal = post("operator", "/api/workspace/change-proposals", {
                "event_id": "recover-" + slot, "slot_id": slot,
                "base_version": current.version, "base_digest": current.digest,
                "value": str(current.payload["canonical_value"]), "source_ref": f"https://source.example/{slot}/current",
                "reason": "Current source verified by its owner", "operation": "READMIT"})
            refs.append({key: proposal[key] for key in ("event_id", "event_digest")})
        path = "/api/workspace/source-readmission-groups/https-recovery"
        body = {"group_id": "https-recovery", "events": refs, "reason": "Recover both sources atomically"}
        post("executor", "/api/workspace/source-readmission-groups", body, status=403)
        detail = post("operator", "/api/workspace/source-readmission-groups", body)
        command = {"group_digest": detail["group"]["digest"], "preview_digest": detail["group"]["preview"]["digest"]}
        owner_command = {**command, "owner_id": owners["product-owner"]}
        post("operator", path + "/approve", owner_command, status=403)
        post("executor", path + "/reject", {**command, "reason": "wrong role"}, status=403)
        post("finance-owner", path + "/approve", owner_command, status=403)
        if decision == "reject":
            rejected = post("product-owner", path + "/reject", {**owner_command, "reason": "Source needs another check"})
            assert rejected["state"] == "REJECTED"
            assert workspace.current_quote().digest == predecessor.digest
            return
        incomplete = post("executor", path + "/apply", command, status=409)
        assert incomplete["detail"]["code"] == "SOURCE_GROUP_APPROVALS_INCOMPLETE"
        deadline = detail["group"]["review_not_before_epoch_ms"] / 1000
        time.sleep(max(0, deadline - time.time()) + 0.02)
        for subject, actor in owners.items():
            post(subject, path + "/approve", {**command, "owner_id": actor})
        post("product-owner", path + "/apply", command, status=403)
        applied = post("executor", path + "/apply", command)
        assert applied["state"] == "APPLIED"
        receipt = applied["outcome"]["rebase_receipt"]
        assert {member["approval"]["actor_id"] for member in receipt["approval_set"]["members"]} == set(owners.values())
        assert len(receipt["transitions"]) == 1
        assert workspace.current_quote().version == f"v{int(predecessor.version[1:]) + 1}"
