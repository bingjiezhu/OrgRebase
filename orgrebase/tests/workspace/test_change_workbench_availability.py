from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


def run_workbench(script: str, *, initial_session=None) -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require('./tests/workspace/console_dom_harness.js');
let run='run:one', failure=null, deferred=null, reads=0, holdProposal=false, proposalResponse=null, expectedPosts=0;
const calls=[];let session=INITIAL_SESSION;
let recordStatus='RECEIVED', allowedActions=['PREVIEW'];
const item=()=>({execution_run_id:run,status:recordStatus,allowed_actions:allowedActions,
 event:{event_id:'event:a',digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:one',
 base_version:'v1',base_digest:'sha256:base',proposal:{payload:{canonical_value:'new'},source_refs:['source:one']}}});
const configuration=()=>({execution_run_id:run,fields:[{slot_id:'product_plan',value_kind:'text',
 current:{version:'v1',digest:'sha256:base',value:'Old plan',state:'CURRENT'},owner_id:'owner:one',allowed_operations:['UPDATE']}]});
window.OrgRebaseClient={session:()=>session,workspace:()=>'workspace:one',async json(path,request={}){
 calls.push({path,request});
 if(request.method==='POST')return holdProposal
  ? new Promise((resolve,reject)=>proposalResponse={resolve:()=>resolve({event_id:request.body.event_id}),reject})
  : {event_id:request.body.event_id};
 if(failure&&path.includes(failure))throw Object.assign(new Error('READ_UNAVAILABLE'),{code:'READ_UNAVAILABLE'});
 if(path.endsWith('/change-options')){
  reads++; const value=configuration();
  if(deferred)return new Promise((resolve,reject)=>deferred.push({resolve:()=>resolve(value),reject}));
  return value;
 }
 if(path.includes('/changes?'))return {items:[item()],next_cursor:null};
 return item();
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
const refresh=()=>window.OrgRebaseChangeWorkbench.refresh();
const selected=()=>window.OrgRebaseChangeWorkbench.selectedDetail();
const unavailable=()=>window.dispatchEvent(new CustomEvent('orgrebase:stateunavailable'));
(async()=>{
 await tick();await tick();
 assert.equal(selected().event.event_id,'event:a');
 nodes.get('change-value').value='Unsaved plan';await nodes.get('change-value').emit('input');
 nodes.get('change-source').value='source:unsaved';await nodes.get('change-source').emit('input');
 SCRIPT
 assert.equal(calls.filter(call=>call.request.method==='POST').length,expectedPosts,'only the explicit proposal may send a command');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('SOURCE', json.dumps((ROOT / 'demo/console/change-workbench.js').read_text()))
        .replace('INITIAL_SESSION', json.dumps(initial_session or {"mode": "local", "principal": None}))
        .replace('SCRIPT', script))


@pytest.mark.parametrize('cause', ['state', 'change-options', 'changes?', 'changes/event'])
def test_unavailable_reads_retire_actionable_details_and_preserve_unsent_draft(cause):
    run_workbench(r'''
 if(CAUSE==='state')unavailable();else {failure=CAUSE;await refresh();}
 assert.equal(selected(),null,'an old detail must not remain actionable after its refresh failed');
 assert.equal(window.OrgRebaseChangeWorkbench.authorityProgress(),null);
 assert.equal(nodes.get('change-proposal-form').hidden,true);
 assert.equal(nodes.get('change-submit').disabled,true);
 assert(!nodes.get('change-detail-body').querySelectorAll('button').some(node=>!node.disabled));
 failure=null;await refresh();
 assert.equal(selected().event.event_id,'event:a');
 assert.equal(nodes.get('change-value').value,'Unsaved plan');
 assert.equal(nodes.get('change-source').value,'source:unsaved');
 assert.equal(nodes.get('change-proposal-form').hidden,false);
 assert.equal(button('Check change impact').disabled,false);
 assert(nodes.get('change-notice').textContent.includes('Current state refreshed'));
 assert.equal(nodes.get('change-notice').dataset.tone,'info');
'''.replace('CAUSE', json.dumps(cause)))


@pytest.mark.parametrize('late_failure', [False, True])
def test_old_refresh_cannot_restore_state_or_overwrite_a_new_refresh(late_failure):
    run_workbench(r'''
 deferred=[];const old=refresh();await tick();
 assert.equal(deferred.length,1);unavailable();
 const current=refresh();await tick();assert.equal(deferred.length,2,'the current scope is not held by the old read');
 deferred[1].resolve();await current;
 const notice=nodes.get('change-notice').textContent;
 if(LATE_FAILURE)deferred[0].reject(new Error('OLD_READ_FAILURE'));else deferred[0].resolve();
 await old;
 assert.equal(selected().event.event_id,'event:a');
 assert.equal(nodes.get('change-notice').textContent,notice,'an obsolete failure cannot overwrite current status');
'''.replace('LATE_FAILURE', json.dumps(late_failure)))


def test_replacing_the_run_clears_the_previous_draft_and_selection_before_loading():
    run_workbench(r'''
 deferred=[];run='run:two';
 window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:run},change_events:[]}}));
 assert.equal(selected(),null);assert.equal(nodes.get('change-proposal-form').hidden,true);
 await tick();assert.equal(deferred.length,1);deferred[0].resolve();await tick();await tick();
 assert.equal(selected().execution_run_id,'run:two');
 assert.equal(nodes.get('change-value').value,'','a draft cannot follow an unrelated execution run');
 assert.equal(nodes.get('change-source').value,'');
''')


@pytest.mark.parametrize('late_failure', [False, True])
def test_proposal_completion_does_not_follow_a_replaced_run(late_failure):
    run_workbench(r'''
 holdProposal=true;expectedPosts=1;
 const submitted=nodes.get('change-proposal-form').emit('submit');await tick();
 assert(proposalResponse);run='run:two';
 window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:run},change_events:[]}}));
 await tick();await tick();
 assert.equal(selected().execution_run_id,'run:two');
 const count=calls.length,notice=nodes.get('change-notice').textContent;
 if(LATE_FAILURE)proposalResponse.reject(new Error('OLD_PROPOSAL_FAILURE'));else proposalResponse.resolve();
 await submitted;
 assert.equal(calls.length,count,'a late proposal response cannot refresh or act in the replacement run');
 assert.equal(selected().execution_run_id,'run:two');
 assert.equal(selected().event.event_id,'event:a');
 assert.equal(nodes.get('change-notice').textContent,notice);
 assert.equal(nodes.get('change-value').value,'');
'''.replace('LATE_FAILURE', json.dumps(late_failure)))


@pytest.mark.parametrize('late_failure', [False, True])
def test_replaced_run_releases_old_busy_state_without_unlocking_a_new_request(late_failure):
    run_workbench(r'''
 holdProposal=true;expectedPosts=2;
 const old=nodes.get('change-proposal-form').emit('submit');await tick();
 const oldResponse=proposalResponse;
 run='run:two';
 window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:run},change_events:[]}}));
 await tick();await tick();
 assert.equal(nodes.get('change-workbench').getAttribute('aria-busy'),'false',
  'a pending request from a retired run must not block the replacement run');
 nodes.get('change-value').value='Current run plan';await nodes.get('change-value').emit('input');
 nodes.get('change-source').value='source:current';await nodes.get('change-source').emit('input');
 const current=nodes.get('change-proposal-form').emit('submit');await tick();
 const currentResponse=proposalResponse;
 assert.notEqual(currentResponse,oldResponse);
 if(LATE_FAILURE)oldResponse.reject(new Error('OLD_PROPOSAL_FAILURE'));else oldResponse.resolve();
 await old;
 assert.equal(nodes.get('change-workbench').getAttribute('aria-busy'),'true',
  'the retired request must not release the active request lock');
 assert(nodes.get('change-submit').disabled);
 currentResponse.reject(new Error('CURRENT_PROPOSAL_FAILURE'));await current;
 assert.equal(nodes.get('change-workbench').getAttribute('aria-busy'),'false');
'''.replace('LATE_FAILURE', json.dumps(late_failure)))


def test_revision_rechecks_current_permission_before_replacing_the_draft():
    run_workbench(r'''
 recordStatus='EXPIRED';allowedActions=['REVISE'];await refresh();
 const revise=button('Revise as new proposal');
 recordStatus='PREVIEWED';allowedActions=['PREVIEW'];
 await revise.emit('click');
 assert.equal(selected().status,'PREVIEWED');
 assert.equal(nodes.get('change-value').value,'Unsaved plan','a disallowed revision cannot replace the unsent draft');
 assert.equal(nodes.get('change-source').value,'source:unsaved');
 assert.equal(nodes.get('change-revision-note').hidden,true);
 assert(nodes.get('change-notice').textContent.includes('state or your permissions changed'));
''')


@pytest.mark.parametrize('changed', ['same_identity', 'issuer', 'subject', 'tenant_id', 'actor_id'])
@pytest.mark.parametrize('expired', [False, True])
def test_proposal_draft_cannot_follow_a_different_authenticated_identity(changed, expired):
    run_workbench(r'''
 const original=structuredClone(session);
 if(EXPIRED){
  session={...session,authenticated:false,principal:null};
  window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
  window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'expired'}}));
 }
 session=structuredClone(original);session.csrf_token='renewed-token';
 if(CHANGED!=='same_identity')session.principal[CHANGED]+=':replacement';
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 await refresh();
 assert.equal(nodes.get('change-value').value,CHANGED==='same_identity'?'Unsaved plan':'',
  'only the same authenticated identity can recover an unsent proposal');
 assert.equal(nodes.get('change-source').value,CHANGED==='same_identity'?'source:unsaved':'');
'''.replace('CHANGED', json.dumps(changed)).replace('EXPIRED', json.dumps(expired)), initial_session={
        "mode": "oidc", "authentication_required": True, "authenticated": True,
        "principal": {"issuer": "https://identity.example", "subject": "person:A",
                      "tenant_id": "tenant:A", "actor_id": "actor:A"},
    })


def test_reauthenticated_session_has_its_own_command_lock():
    run_workbench(r'''
 holdProposal=true;expectedPosts=2;
 const old=nodes.get('change-proposal-form').emit('submit');await tick();
 const oldResponse=proposalResponse,original=structuredClone(session);
 session={...session,authenticated:false,principal:null};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'expired'}}));
 session=structuredClone(original);session.csrf_token='renewed-token';
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 await refresh();
 assert.equal(nodes.get('change-workbench').getAttribute('aria-busy'),'false');
 assert.equal(nodes.get('change-value').value,'Unsaved plan');
 const current=nodes.get('change-proposal-form').emit('submit');await tick();
 const currentResponse=proposalResponse;assert.notEqual(currentResponse,oldResponse);
 oldResponse.resolve();await old;
 assert.equal(nodes.get('change-workbench').getAttribute('aria-busy'),'true');
 assert(nodes.get('change-submit').disabled);
 currentResponse.reject(new Error('CURRENT_PROPOSAL_FAILURE'));await current;
 assert.equal(nodes.get('change-workbench').getAttribute('aria-busy'),'false');
 const writes=calls.filter(call=>call.request.method==='POST');
 assert.equal(writes[0].request.body.event_id,writes[1].request.body.event_id,
  'explicit retry of an unchanged draft retains its idempotency key');
''', initial_session={
        "mode": "oidc", "authentication_required": True, "authenticated": True,
        "principal": {"issuer": "https://identity.example", "subject": "person:A",
                      "tenant_id": "tenant:A", "actor_id": "actor:A"},
    })
