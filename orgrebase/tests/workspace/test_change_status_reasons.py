from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.domain import VersionedObject
from orgrebase.workspace.approval_authority import CoordinationInput, revoke_delegation
from orgrebase.workspace.change_proposals import change_detail, submit_change
from orgrebase.workspace.models import ChangeEvent
from orgrebase.workspace.owner_change import activate_owner_change
from orgrebase.workspace.rebuild import WorkspaceWorkflowClock
from orgrebase.workspace.routes import change_router
from tests.workspace.test_apply_recovery import committed_without_outcome as committed_without_outcome
from tests.workspace.test_approval_authority import delegated as delegated
from tests.workspace.test_approval_authority import delegated_approval
from tests.workspace.test_change_proposals import apply, command
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_continuous_changes import proposal
from tests.workspace.test_owner_migration import confirmed, pending
from tests.workspace.test_owner_migration import migration_workspace as migration_workspace
from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


def assert_projection(workspace, event_id, status, reason):
    before = tuple(workspace.store.connection.iterdump())
    assert workspace._change_status(event_id) == status
    detail = change_detail(workspace, event_id)
    assert detail["status"] == status
    assert detail["status_reason"] == reason
    if status in {"STALE", "EXPIRED", "SCHEDULED"}:
        assert not {"APPROVE", "APPLY"}.intersection(detail["allowed_actions"])
    assert tuple(workspace.store.connection.iterdump()) == before
    return detail


@pytest.mark.parametrize("scheduled", [False, True])
def test_proposal_validity_reports_its_actual_boundary(workspace, scheduled):
    event = proposal(workspace, "timed", "product_plan", "Revised plan")
    now = utc_datetime(workspace.clock.now())
    candidate = VersionedObject.model_validate({
        **event.proposal.model_dump(mode="json", exclude={"digest"}),
        "valid_from": timestamp(now + timedelta(minutes=5)) if scheduled else timestamp(now),
        "valid_to": timestamp(now + timedelta(minutes=10)),
    })
    event = ChangeEvent.model_validate({**event.model_dump(mode="json", exclude={"digest"}), "proposal": candidate})
    workspace.register_change(event)
    if not scheduled:
        workspace.clock = FrozenClock(candidate.valid_to)
    assert_projection(workspace, event.event_id, "SCHEDULED" if scheduled else "EXPIRED",
                      "PROPOSAL_NOT_YET_EFFECTIVE" if scheduled else "PROPOSAL_VALIDITY_EXPIRED")


def test_runtime_change_is_explained_in_the_read_api_without_new_authority(workspace):
    submit_change(workspace, command(workspace))
    workspace.preview_change("edit-1")
    workspace.advisory_factory = SimpleNamespace(configuration_binding={"model_id": "controlled-new-model"})
    detail = assert_projection(workspace, "edit-1", "EXPIRED", "RUNTIME_IMPLEMENTATION_CHANGED")
    assert detail["runtime_compatibility"]["decision"] == "REPLAN_REQUIRED"
    application = FastAPI()
    application.include_router(change_router(lambda: workspace))
    before = tuple(workspace.store.connection.iterdump())
    with TestClient(application) as client:
        response = client.get("/api/workspace/changes/edit-1")
    assert response.status_code == 200
    assert response.json()["status_reason"] == "RUNTIME_IMPLEMENTATION_CHANGED"
    assert response.json()["allowed_actions"] == detail["allowed_actions"]
    assert tuple(workspace.store.connection.iterdump()) == before


def test_preview_deadline_is_distinct_from_proposal_validity(workspace):
    submit_change(workspace, command(workspace))
    bundle = workspace.preview_change("edit-1")
    workspace.clock = FrozenClock(bundle.run_envelope.expires_at)
    assert_projection(workspace, "edit-1", "EXPIRED", "PREVIEW_VALIDITY_EXPIRED")


def test_approval_reports_runtime_replan_instead_of_time_expiry(workspace):
    submit_change(workspace, command(workspace))
    bundle = workspace.preview_change("edit-1")
    workspace.advisory_factory = SimpleNamespace(configuration_binding={"model_id": "controlled-new-model"})
    before = workspace.store.audit_head()
    with pytest.raises(RuntimeError, match=r"^WORKSPACE_RUNTIME_REPLAN_REQUIRED:RUNTIME_IMPLEMENTATION_CHANGED$"):
        workspace.approve_change("edit-1", actor_id=workspace.change_owner["edit-1"],
                                 preview_digest=bundle.preview.digest)
    assert workspace._approval_record("edit-1") is None
    assert workspace.store.audit_head() == before


def test_approval_preserves_time_expiry_error(workspace):
    submit_change(workspace, command(workspace))
    bundle = workspace.preview_change("edit-1")
    workspace.clock = FrozenClock(bundle.run_envelope.expires_at)
    with pytest.raises(RuntimeError, match=r"^WORKSPACE_PROPOSAL_EXPIRED$"):
        workspace.approve_change("edit-1", actor_id=workspace.change_owner["edit-1"],
                                 preview_digest=bundle.preview.digest)


def test_shorter_approval_deadline_is_reported_without_changing_the_preview(workspace, monkeypatch):
    submit_change(workspace, command(workspace))
    bundle = workspace.preview_change("edit-1")
    now = workspace.clock.now()
    expiry = timestamp(utc_datetime(now) + timedelta(seconds=30))
    monkeypatch.setattr(workspace, "_clock", lambda kind: WorkspaceWorkflowClock(
        approved_at=now, apply_at=now, expires_at=expiry))
    workspace.approve_change("edit-1", actor_id=workspace.change_owner["edit-1"], preview_digest=bundle.preview.digest)
    workspace.clock = FrozenClock(expiry)
    assert_projection(workspace, "edit-1", "EXPIRED", "APPROVAL_VALIDITY_EXPIRED")


@pytest.mark.parametrize("same_field", [False, True])
def test_intervening_apply_distinguishes_base_version_from_snapshot_change(workspace, same_field):
    submit_change(workspace, command(workspace, "waiting"))
    workspace.preview_change("waiting")
    submit_change(workspace, command(workspace, "other", slot="product_plan" if same_field else "currency",
                                     value="Competing plan" if same_field else "GBP"))
    apply(workspace, "other")
    assert_projection(workspace, "waiting", "STALE",
                      "CHANGE_BASE_VERSION_CHANGED" if same_field else "CHANGE_SNAPSHOT_CHANGED")


def test_source_invalidation_explains_the_changed_base_state(workspace):
    submit_change(workspace, command(workspace))
    workspace.changes.invalidate("product_plan", "source:permission-revoked", "PERMISSION_DENIED")
    assert_projection(workspace, "edit-1", "STALE", "CHANGE_BASE_STATE_CHANGED")


def test_owner_handover_explains_replanning(migration_workspace):
    workspace, principal, *_ = migration_workspace
    with principal("operator"):
        pending(workspace, "waiting")
    transfer = confirmed(workspace, principal)
    with principal("successor"):
        activate_owner_change(workspace, "handover", transfer)
    assert_projection(workspace, "waiting", "EXPIRED", "CHANGE_OWNER_REPLAN_REQUIRED")


def test_revoked_delegated_approval_explains_authority_change(delegated):
    workspace, event, _, _, identity, _ = delegated
    delegated_approval(delegated)
    with identity("owner"):
        revoke_delegation(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Owner resumes review"))
    detail = assert_projection(workspace, "edit-1", "EXPIRED", "APPROVAL_AUTHORITY_CHANGED")
    assert detail["authority"]["blocked_reason"] == "DELEGATION_REVOKED"


def test_applied_result_does_not_inherit_current_expiry_or_runtime_drift(workspace):
    submit_change(workspace, command(workspace))
    apply(workspace, "edit-1")
    workspace.clock = FrozenClock(timestamp(utc_datetime(workspace.clock.now()) + timedelta(days=2)))
    workspace.advisory_factory = SimpleNamespace(configuration_binding={"model_id": "controlled-new-model"})
    detail = assert_projection(workspace, "edit-1", "APPLIED", None)
    assert detail["runtime_compatibility"]["decision"] == "READ_ONLY_HISTORICAL"


def test_committed_recovery_does_not_inherit_current_expiry_or_runtime_drift(committed_without_outcome):
    workspace, approval = committed_without_outcome
    workspace.clock = FrozenClock(timestamp(utc_datetime(workspace.clock.now()) + timedelta(days=2)))
    workspace.advisory_factory = SimpleNamespace(configuration_binding={"model_id": "controlled-new-model"})
    detail = assert_projection(workspace, "currency", "RECOVERY_REQUIRED", None)
    assert detail["allowed_actions"] == ["APPLY"]
    assert detail["approval"]["approval_digest"] == approval["approval_digest"]


def test_readonly_ui_explains_runtime_expiry_in_both_languages_and_hides_it_for_committed_history(workspace):
    submit_change(workspace, command(workspace))
    bundle = workspace.preview_change("edit-1")
    workspace.approve_change("edit-1", actor_id=workspace.change_owner["edit-1"], preview_digest=bundle.preview.digest)
    approved = change_detail(workspace, "edit-1")
    script = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
let item={execution_run_id:'run:one',status:'EXPIRED',status_reason:'RUNTIME_IMPLEMENTATION_CHANGED',allowed_actions:['REVISE'],
 event:{event_id:'event:one',digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:one',base_version:'v1',base_digest:'sha256:base',proposal:{payload:{canonical_value:'new'},source_refs:['source:one']}}};
const session={mode:'local',principal:null};
window.OrgRebaseClient={session:()=>session,workspace:()=> 'workspace:one',async json(path,request={}){
 assert.equal(request.method,undefined,'reason display cannot dispatch work');
 if(path.endsWith('/change-options'))return {execution_run_id:item.execution_run_id,fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
const walk=node=>[node,...node.children.flatMap(walk)],text=()=>walk(nodes.get('change-detail-body')).map(node=>node.textContent||'').join('\n');
(async()=>{
 await tick();await tick();assert(text().includes('Execution code or model configuration changed.'));
 assert(walk(nodes.get('change-detail-body')).some(node=>node.title==='RUNTIME_IMPLEMENTATION_CHANGED'));
 document.documentElement.lang='zh';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
 assert(text().includes('执行代码或模型配置已更新'));assert(text().includes('重新预演与批准'));
 const steps=()=>window.OrgRebaseChangeWorkbench.authorityProgress().steps;
 for(const state of ['EXPIRED','STALE']){
  item.status=state;await window.OrgRebaseChangeWorkbench.select(item.event.event_id);
  assert.equal(steps()[4].status,'blocked');assert.equal(steps()[5].status,'blocked');
  assert(steps()[4].text.includes('查看阻断原因'));assert(steps()[5].text.includes('不可生效'));
 }
 for(const state of ['APPLIED','RECOVERY_REQUIRED']){
  item.status=state;item.allowed_actions=[];await window.OrgRebaseChangeWorkbench.select(item.event.event_id);
  assert(!text().includes('执行代码或模型配置已更新'),'historical committed effects cannot be presented as expired');
 }
 item=APPROVED_RECORD;await window.OrgRebaseChangeWorkbench.refresh();
 const prior=JSON.stringify(steps().slice(0,4));
 for(const state of ['EXPIRED','STALE']){
  item.status=state;item.status_reason='APPROVAL_AUTHORITY_CHANGED';item.allowed_actions=['REVISE'];
  await window.OrgRebaseChangeWorkbench.select(item.event.event_id);
  assert.equal(steps()[4].status,'complete','a bound recorded approval remains a historical fact');
  assert.equal(steps()[5].status,'blocked');assert.equal(JSON.stringify(steps().slice(0,4)),prior,'invalidation does not rewrite candidate or review history');
 }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    run_node(script.replace("HARNESS", json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js")))
             .replace("APPROVED_RECORD", json.dumps(approved))
             .replace("SOURCE", json.dumps((ROOT / "demo/console/change-workbench.js").read_text())))
