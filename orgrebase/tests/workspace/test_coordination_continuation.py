from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.workspace.approval_authority import (
    CoordinationInput,
    authority_detail,
    escalate_change,
    revoke_delegation,
)
from orgrebase.workspace.change_proposals import change_detail
from tests.workspace.test_approval_authority import delegated as delegated
from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("transition", ["revocation", "expiry", "membership", "escalation"])
def test_authority_revision_tracks_same_status_changes_without_writing(delegated, transition):
    workspace, event, members, _, identity, _ = delegated

    def state_event():
        return next(item for item in workspace.state()["change_events"] if item["event_id"] == "edit-1")

    before = state_event()
    quote_digest = workspace.current_quote().digest
    with identity("owner"):
        owner_revision = authority_detail(workspace, "edit-1")["revision"]
    with identity("delegate"):
        assert authority_detail(workspace, "edit-1")["revision"] == owner_revision
    assert before["authority_revision"] == owner_revision
    assert state_event() == before
    if transition == "revocation":
        with identity("owner"):
            revoke_delegation(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Owner resumes review"))
    elif transition == "expiry":
        workspace.clock = FrozenClock(timestamp(utc_datetime(workspace.clock.now()) + timedelta(minutes=16)))
    elif transition == "membership":
        members.pop("delegate")
    else:
        with identity("operator", "operator"):
            escalate_change(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Evidence needs a responsible reviewer"))
    chain_before_reads = workspace.store.verify_event_chain()
    after = state_event()
    detail = change_detail(workspace, "edit-1")
    assert after["status"] == before["status"] == "RECEIVED"
    assert after["event_digest"] == before["event_digest"]
    assert after["authority_revision"] != before["authority_revision"]
    assert after["authority_revision"] == detail["authority"]["revision"]
    assert state_event() == after
    assert workspace.store.verify_event_chain() == chain_before_reads
    assert workspace.current_quote().digest == quote_digest
    assert workspace._approval_record("edit-1") is None


def test_poll_revision_is_independent_of_the_visible_member_directory(delegated):
    workspace, _, _, _, identity, _ = delegated
    directory_reads = []

    def directory(action):
        directory_reads.append(action)
        return [{"subject": "delegate", "actor_id": "user:delegate"}]

    workspace.members_for_action = directory
    with identity("owner"):
        owner = authority_detail(workspace, "edit-1")
        assert len(owner["eligible_delegates"]) == 1
        polled = next(item for item in workspace.state()["change_events"] if item["event_id"] == "edit-1")
    with identity("delegate"):
        delegate = authority_detail(workspace, "edit-1")
    assert delegate["eligible_delegates"] == []
    assert owner["revision"] == delegate["revision"] == polled["authority_revision"]
    assert directory_reads == ["approve"], "state polling does not enumerate the member directory"


def _run(script: str) -> None:
    run_node(script.replace("HARNESS", json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js")))
             .replace("SOURCE", json.dumps((ROOT / "demo/console/change-workbench.js").read_text())))


def test_same_status_authority_refresh_preserves_review_and_discards_late_selection():
    _run(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS),reads=[];
const make=(id,digit)=>({execution_run_id:'run:one',status:'RECEIVED',allowed_actions:['REJECT'],
 authority:{revision:'revision:initial',active_owner_id:'owner:one',delegation_active:false},
 event:{event_id:id,digest:'sha256:'+digit.repeat(64),slot_id:'product_plan',owner_id:'owner:one',base_version:'v1',base_digest:'sha256:'+'c'.repeat(64),
 proposal:{id:'claim:plan',version:'v2',payload:{canonical_value:'new'},source_refs:['source:one']}}});
const a=make('event:a','a'),b=make('event:b','b');
const session={mode:'local',principal:null},pending=new Map();let defer=false;
window.OrgRebaseClient={session:()=>session,async json(path,request={}){
 assert.equal(request.method,undefined,'poll synchronization must not issue commands');
 if(path.endsWith('/change-options'))return {execution_run_id:'run:one',fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(a),structuredClone(b)],next_cursor:null};
 const item=decodeURIComponent(path.split('/').at(-1))==='event:a'?a:b;reads.push(item.event.event_id);
 return defer?new Promise(resolve=>pending.set(item.event.event_id,()=>resolve(structuredClone(item)))):structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
const dispatch=(override={})=>window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:'run:one'},
 change_events:[a,b].map(item=>({event_id:item.event.event_id,event_digest:item.event.digest,status:item.status,authority_revision:item.authority.revision})),...override}}));
const selected=()=>window.OrgRebaseChangeWorkbench.selectedDetail();
(async()=>{
 await tick();await tick();dispatch();dispatch();await tick();assert.deepEqual(reads,['event:a']);
 nodes.get('change-detail-reason').value='Evidence still missing';
 a.authority.revision='revision:delegated';a.authority.active_owner_id='reviewer:backup';a.authority.delegation_active=true;
 a.active_owner_id='reviewer:backup';defer=true;
 dispatch({change_events:[{event_id:'event:a',event_digest:'foreign',status:'RECEIVED',authority_revision:'changed'}]});
 await tick();assert.equal(reads.length,1,'authority refresh requires the exact event digest');
 dispatch();dispatch();await tick();assert.equal(reads.length,2,'one read for a new authority revision');
 pending.get('event:a')();await tick();await tick();
 assert.equal(selected().authority.delegation_active,true);assert.equal(selected().active_owner_id,'reviewer:backup');
 assert.equal(nodes.get('change-detail-reason').value,'Evidence still missing');
 dispatch();dispatch();await tick();assert.equal(reads.length,2,'unchanged revisions do not poll detail');
 a.authority.revision='revision:revoked';a.authority.delegation_active=false;a.active_owner_id='owner:one';
 dispatch();await tick();assert.equal(reads.length,3);
 const switching=window.OrgRebaseChangeWorkbench.select('event:b');await tick();
 dispatch();dispatch();await tick();assert.equal(reads.length,4);
 pending.get('event:b')();await switching;pending.get('event:a')();await tick();await tick();
 assert.equal(selected().event.event_id,'event:b','late authority update cannot replace another selected change');
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.parametrize("context_change", ["session", "workspace", "run", "selection"])
@pytest.mark.parametrize("failed", [False, True])
def test_late_coordination_response_cannot_update_another_context(context_change, failed):
    _run(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS),calls=[];
let session={mode:'oidc',principal:{actor_id:'owner:one'}},workspace='workspace:one',run='run:one',settle;
const make=id=>({execution_run_id:'run:one',status:'RECEIVED',allowed_actions:['ESCALATE'],
 authority:{owner_id:'owner:one',active_owner_id:'owner:one'},event:{event_id:id,digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:one',base_version:'v1',base_digest:'sha256:base',proposal:{payload:{canonical_value:'new'},source_refs:['source:one']}}});
const a=make('event:a'),b=make('event:b');
window.OrgRebaseClient={session:()=>session,workspace:()=>workspace,async json(path,request={}){
 calls.push({path,request});
 if(request.method==='POST')return new Promise((resolve,reject)=>{settle=()=>FAILED?reject(new Error('COORDINATION_FAILED')):resolve({});});
 if(path.endsWith('/change-options'))return {execution_run_id:run,fields:[]};
 if(path.includes('/changes?'))return {items:run==='run:one'?[structuredClone(a),structuredClone(b)]:[],next_cursor:null};
 return structuredClone(decodeURIComponent(path.split('/').at(-1))==='event:a'?a:b);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
(async()=>{
 await tick();await tick();nodes.get('change-escalate-reason').value='Need another reviewer';
 await button('Request escalation').parent.emit('submit');await tick();
 assert.equal(calls.filter(call=>call.request.method==='POST').length,1);
 if(CHANGE==='session'){session={...session};window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));}
 if(CHANGE==='workspace')workspace='workspace:two';
 if(CHANGE==='run'){
  run='run:two';window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:run},change_events:[]}}));
  assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail(),null,'a different run immediately retires the previous selection');
  await tick();await tick();
 }
 if(CHANGE==='selection')await window.OrgRebaseChangeWorkbench.select('event:b');
 const count=calls.length,notice=nodes.get('change-notice').textContent;
 settle();await tick();await tick();
 assert.equal(calls.length,count,'late coordination must not refresh a different context');
 assert.equal(nodes.get('change-notice').textContent,notice,'late success or error must not label a different context');
 if(CHANGE==='session')assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail(),null);
 if(CHANGE==='run')assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail(),null,'a late coordination result cannot restore the previous run');
 if(CHANGE==='selection')assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail().event.event_id,'event:b');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace("CHANGE", json.dumps(context_change)).replace("FAILED", json.dumps(failed)))


def test_failed_candidate_next_step_follows_owner_then_revision_permissions():
    _run(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
const item={execution_run_id:'run:one',status:'RECEIVED',allowed_actions:[],active_owner_id:'reviewer:backup',
 advisory_attempt:{state:'FAILED',public_error_code:'WORKSPACE_ADVISORY_MODEL_INCOMPLETE'},
 event:{event_id:'event:a',digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:one',base_version:'v1',base_digest:'sha256:base',proposal:{payload:{canonical_value:'new'},source_refs:['source:one']}}};
window.OrgRebaseClient={session:()=>({mode:'local',principal:null}),async json(path,request={}){
 assert.equal(request.method,undefined,'next-step guidance must not dispatch work');
 if(path.endsWith('/change-options'))return {execution_run_id:'run:one',fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
const walk=node=>[node,...node.children.flatMap(walk)],text=()=>walk(nodes.get('change-advisory-attempt')).map(node=>node.textContent||'').join('\n');
(async()=>{
 await tick();await tick();assert(text().includes('Next, reviewer:backup'));assert(text().includes('WORKSPACE_ADVISORY_MODEL_INCOMPLETE'));
 item.status='REJECTED';item.allowed_actions=['REVISE'];await window.OrgRebaseChangeWorkbench.select('event:a');
 assert(text().includes('use “Revise as new proposal” below'));assert(!text().includes('records a rejection'),'already rejected work does not ask for another rejection');
 item.allowed_actions=[];await window.OrgRebaseChangeWorkbench.select('event:a');
 assert(text().includes('member with proposal permission'));assert(!text().includes('below'),'read-only viewers are not pointed to an unavailable control');
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.parametrize("failed_read", ["options", "list", "detail"])
def test_committed_coordination_keeps_refresh_errors_visible_without_repeating_the_command(failed_read):
    _run(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS),calls=[];
let committed=false,failRead=true;
const session={mode:'oidc',principal:{actor_id:'owner:one'}};
const item={execution_run_id:'run:one',status:'RECEIVED',allowed_actions:['ESCALATE'],
 authority:{owner_id:'owner:one',active_owner_id:'owner:one',escalation:null},
 event:{event_id:'event:a',digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:one',base_version:'v1',base_digest:'sha256:base',proposal:{payload:{canonical_value:'new'},source_refs:['source:one']}}};
window.OrgRebaseClient={session:()=>session,workspace:()=> 'workspace:one',async json(path,request={}){
 calls.push({path,request});
 if(request.method==='POST'){committed=true;item.authority.escalation={reason:request.body.reason};item.allowed_actions=[];return {};}
 const kind=path.endsWith('/change-options')?'options':path.includes('/changes?')?'list':'detail';
 if(committed&&failRead&&kind===FAILED_READ)throw new Error('COORDINATION_REFRESH_READ_FAILED');
 if(kind==='options')return {execution_run_id:'run:one',fields:[]};
 if(kind==='list')return {items:[structuredClone(item)],next_cursor:null};return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
(async()=>{
 await tick();await tick();nodes.get('change-escalate-reason').value='Ask the owner to assign review';
 await button('Request escalation').parent.emit('submit');await tick();await tick();
 assert.equal(committed,true);assert.equal(calls.filter(call=>call.request.method==='POST').length,1);
 const notice=nodes.get('change-notice');
 assert.equal(notice.dataset.tone,'error');assert(notice.textContent.includes('COORDINATION_REFRESH_READ_FAILED'));
 assert(!notice.textContent.includes('Coordination recorded'),'a successful write cannot hide a failed confirmation read');
 assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail(),null,'failed refresh retires stale authority and action permissions');
 assert(!nodes.get('change-detail-body').querySelectorAll('button').some(node=>!node.disabled),'failed confirmation cannot leave the old coordination controls actionable');
 assert.equal(await window.OrgRebaseChangeWorkbench.refresh(),false,'all required read failures propagate, including the selected detail');
 failRead=false;assert.equal(await window.OrgRebaseChangeWorkbench.refresh(),true);
 assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail().authority.escalation.reason,'Ask the owner to assign review');
 assert.equal(calls.filter(call=>call.request.method==='POST').length,1,'recovery reads never repeat coordination');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace("FAILED_READ", json.dumps(failed_read)))
