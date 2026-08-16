from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError, ObjectState
from orgrebase.service import OrgRebaseService


def test_dependency_tool_is_real_audited_and_idempotent(service: OrgRebaseService) -> None:
    result = service.invoke_dependency_tool(
        actor_id="gtm-steward",
        target_ids=("work:sales_quote_a", "work:partner_brief_e"),
        graph_revision=service.fixture.revisions["graph"],
        idempotency_key="test-tool-call-01",
    )
    replay = service.invoke_dependency_tool(
        actor_id="gtm-steward",
        target_ids=("work:sales_quote_a", "work:partner_brief_e"),
        graph_revision=service.fixture.revisions["graph"],
        idempotency_key="test-tool-call-01",
    )
    assert replay == result
    assert result["receipt"]["status"] == "SUCCEEDED"
    assert result["receipt"]["evidence_class"] == "LOCAL_DETERMINISTIC"
    assert result["result"]["edges"][0]["target_id"] == "work:sales_quote_a"
    assert service.store.verify_event_chain()["status"] == "PASS"


def test_dependency_tool_fails_closed_on_auth_revision_and_target(service: OrgRebaseService) -> None:
    with pytest.raises(AuthorizationError):
        service.invoke_dependency_tool(
            actor_id="legal-steward",
            target_ids=("work:sales_quote_a",),
            graph_revision=service.fixture.revisions["graph"],
            idempotency_key="denied-tool-call",
        )
    with pytest.raises(RuntimeError, match="GRAPH_REVISION_MISMATCH"):
        service.invoke_dependency_tool(
            actor_id="gtm-steward",
            target_ids=("work:sales_quote_a",),
            graph_revision="graph:drifted@r2",
            idempotency_key="drifted-tool-call",
        )
    with pytest.raises(ValueError, match="INVALID_TARGET"):
        service.invoke_dependency_tool(
            actor_id="gtm-steward",
            target_ids=("source:legal.customer-contract",),
            graph_revision=service.fixture.revisions["graph"],
            idempotency_key="invalid-tool-call",
        )


def test_conflict_resolution_uses_authority_not_agent_vote(service: OrgRebaseService) -> None:
    result = service.conflict_drill()
    receipt = result["receipt"]
    assert receipt.admitted_candidate_id == "candidate:product-launch-date@v8"
    assert receipt.rejected[0]["reason"] == "NON_AUTHORITATIVE_DOMAIN"
    assert receipt.target_writes == 0
    assert result["target_state_unchanged"] is True


def test_freshness_failure_drill_proves_zero_target_writes(service: OrgRebaseService) -> None:
    result = service.freshness_failure_drill()
    receipt = result["receipt"]
    assert receipt.error_code == "PREVIEW_EXPIRED"
    assert receipt.target_writes == 0
    assert receipt.before_state_digest == receipt.after_state_digest
    assert result["current_state"]["claim:product.launch_date"]["version"] == "v7"


def test_compensating_rollback_is_immutable_atomic_and_receipted(service: OrgRebaseService) -> None:
    applied = service.apply()["receipt"]
    result = service.rollback()
    receipt = result["receipt"]
    state = result["current_state"]
    assert receipt.compensates_rebase_receipt == applied.digest
    assert receipt.status == "ROLLED_BACK_PENDING_REBASE"
    assert receipt.authoritative_claim_unchanged is True
    assert receipt.metrics["history_rows_deleted"] == 0
    assert receipt.metrics["authoritative_claims_changed"] == 0
    assert state["claim:product.launch_date"]["version"] == "v8"
    assert service.store.get_object("claim:product.launch_date").payload["canonical_value"] == "2026-09-15"
    assert service.store.get_object("claim:product.launch_date", "v8").state == ObjectState.CURRENT
    assert state["work:sales_quote_a"]["version"] == "v3"
    assert state["work:support_doc_b"]["version"] == "v3"
    assert state["work:sales_quote_a"]["state"] == "ROLLED_BACK_PENDING_REBASE"
    assert state["work:support_doc_b"]["state"] == "ROLLED_BACK_PENDING_REBASE"
    assert state["work:partner_brief_e"]["state"] == "REVIEW_REQUIRED"
    assert state["skill:enterprise-launch-readiness"]["version"] == "1.2"
    assert state["skill:enterprise-launch-readiness"]["state"] == "REQUALIFICATION_REQUIRED"
    candidate_state = service.store.get_object(
        "skill:enterprise-launch-readiness", "1.3"
    ).state
    assert candidate_state == ObjectState.QUARANTINED
    assert service.store.verify_event_chain()["status"] == "PASS"


def test_rollback_binding_idempotency_and_tamper_detection(service: OrgRebaseService) -> None:
    rebase = service.apply()["receipt"]
    plan = service.rollback_workflow.plan(rebase)
    approval = service.rollback_workflow.approve(rebase, plan)
    wrong = approval.model_copy(update={"rebase_receipt_digest": "sha256:" + "0" * 64, "digest": ""})
    with pytest.raises(FreshnessError, match="not bound"):
        service.rollback_workflow.apply(rebase_receipt=rebase, plan=plan, approval=wrong)
    first = service.rollback_workflow.apply(
        rebase_receipt=rebase, plan=plan, approval=approval
    )
    second = service.rollback_workflow.apply(
        rebase_receipt=rebase, plan=plan, approval=approval
    )
    assert first == second
    payload = first.model_dump(mode="json")
    assert service.verify_rollback_receipt(payload)["status"] == "PASS"
    tampered = copy.deepcopy(payload)
    tampered["metrics"]["history_rows_deleted"] = 1
    with pytest.raises(IntegrityError, match="digest"):
        service.verify_rollback_receipt(tampered)


def test_rollback_requires_independent_authorized_approver(service: OrgRebaseService) -> None:
    rebase = service.apply()["receipt"]
    plan = service.rollback_workflow.plan(rebase)
    with pytest.raises(AuthorizationError, match="independent"):
        service.rollback_workflow.approve(
            rebase,
            plan,
            actor_id=rebase.approval_actor_id,
        )

    approval = service.rollback_workflow.approve(rebase, plan)
    unauthorized = approval.model_copy(update={"authority_scope": (), "digest": ""})
    with pytest.raises(AuthorizationError, match="lacks"):
        service.rollback_workflow.apply(
            rebase_receipt=rebase,
            plan=plan,
            approval=unauthorized,
            idempotency_key="rollback:unauthorized@r1",
        )


def test_rollback_plan_fails_closed_on_target_drift(service: OrgRebaseService) -> None:
    rebase = service.apply()["receipt"]
    plan = service.rollback_workflow.plan(rebase)
    approval = service.rollback_workflow.approve(rebase, plan)
    with service.store.transaction() as connection:
        service.store.transition_current(
            connection, "work:sales_quote_a", ObjectState.REVIEW_REQUIRED
        )
    before = service.current_view()
    with pytest.raises(FreshnessError, match="drifted"):
        service.rollback_workflow.apply(
            rebase_receipt=rebase,
            plan=plan,
            approval=approval,
            idempotency_key="rollback:drifted@r1",
        )
    assert service.current_view() == before


def test_observability_is_otlp_mappable_and_content_free(service: OrgRebaseService) -> None:
    bundle = service.observability()
    spans = bundle["traces"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    serialized = json.dumps(bundle, ensure_ascii=False)
    assert len(spans) == 7
    assert bundle["privacy"]["content_capture"] is False
    assert "orgrebase.read_dependency_evidence" in serialized
    agent_operations = {
        attribute["value"]["stringValue"]
        for span in spans
        if span["name"].startswith("agent.run/")
        for attribute in span["attributes"]
        if attribute["key"] == "gen_ai.operation.name"
    }
    assert agent_operations == {"invoke_agent"}
    assert "2026-09-15" not in serialized
    assert "raw_text" not in serialized
    assert bundle["logs"]["resourceLogs"]
    assert bundle["metrics"]["resourceMetrics"]


def test_goai_demo_endpoints_are_executable() -> None:
    service = OrgRebaseService()
    with TestClient(create_app(service)) as client:
        contract = client.get("/api/tools/v1/dependency-evidence/contract").json()
        assert contract["permissions"]["canonical_state_write"] is False
        git_contract = client.get("/api/tools/v1/git-artifact/contract").json()
        assert git_contract["auth"]["agent_credentials"] is False
        assert git_contract["permissions"]["effect"] == "write_reversible_external_artifact"
        tool = client.post(
            "/api/tools/v1/dependency-evidence",
            headers={"X-OrgRebase-Actor": "gtm-steward"},
            json={
                "target_ids": ["work:sales_quote_a"],
                "graph_revision": service.fixture.revisions["graph"],
                "idempotency_key": "api-tool-call-01",
            },
        )
        assert tool.status_code == 200
        assert client.post("/api/demo/conflict").json()["receipt"]["target_writes"] == 0
        assert client.post("/api/demo/failure").json()["receipt"]["error_code"] == "PREVIEW_EXPIRED"
        rollback = client.post("/api/demo/rollback").json()
        assert rollback["receipt"]["status"] == "ROLLED_BACK_PENDING_REBASE"
        verified = client.post(
            "/api/receipts/rollback/verify", json=rollback["receipt"]
        ).json()
        assert verified["status"] == "PASS"
    service.store.close()
