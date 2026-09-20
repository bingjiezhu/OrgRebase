from __future__ import annotations

import json
import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require('./tests/workspace/console_dom_harness.js');
const calls=[];
let reviewClock=0;
let session={mode:'local',principal:null},workspace='workspace:one',approveError=null,applyError=null;
let approvalWait=null,applyWait=null,detailWait=null,allowApply=true,alterDetail=value=>value,applyObserved=()=>{};
const item={execution_run_id:'run:one',event:{event_id:'change:one',digest:'event:one',slot_id:'product_plan',
 owner_id:'owner:one',base_version:'v1',base_digest:'base:one',proposal:{payload:{canonical_value:'New plan'},source_refs:['source:one']}},
 status:'PREVIEWED',allowed_actions:['APPROVE'],preview:{preview_digest:'preview:one',
 review_gate:{gate:{preview_digest:'preview:one',not_before_epoch_ms:0}},
 bundle:{minimal_rebase_certificate:{effects:[{target_id:'work:quote',disposition:'REBUILD'}]}}}};
const configuration={execution_run_id:'run:one',fields:[]};
window.OrgRebaseClient={session:()=>session,workspace:()=>workspace,async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path==='/api/workspace/change-options')return structuredClone(configuration);
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};
 if(path.endsWith('/review-observation'))return {};
 if(path.includes('/approve/')){
  if(approvalWait)await approvalWait;
  if(approveError)throw approveError;
  assert.equal(request.body.preview_digest,'preview:one');
  item.status='APPROVED';item.allowed_actions=allowApply?['APPLY']:[];
  item.approval={approval_digest:'approval:one'};return structuredClone(item.approval);
 }
 if(path.includes('/apply/')){
  assert.equal(request.body.approval_digest,'approval:one');
  if(applyWait)await applyWait;
  applyObserved();
  if(applyError)throw applyError;
  item.status='APPLIED';item.allowed_actions=[];return {};
 }
 if(item.status==='APPROVED'&&detailWait)await detailWait;
 return alterDetail(structuredClone(item));
}};
vm.runInNewContext(fs.readFileSync('demo/console/change-workbench.js','utf8'),
 {window,document,CustomEvent,performance:{now:()=>reviewClock},crypto:{randomUUID:()=>String(calls.length)}});
const commands=()=>calls.filter(call=>call.path.includes('/approve/')||call.path.includes('/apply/'));
async function ready(){
 await tick();await tick();
 window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:'run:one'}}}));
 nodes.get('change-review-ack').checked=true;await nodes.get('change-review-ack').emit('change');
 assert.equal(button('Approve exact proposal').disabled,false);
}
'''


SCENARIOS = {
    "automatic_apply_does_not_duplicate_approval_review_time": r'''
nodes.get('change-detail').append(nodes.get('change-detail-title'));
nodes.get('change-detail-title').focus();window.dispatchEvent(new CustomEvent('focus'));
reviewClock=2300;
await button('Approve exact proposal').emit('click');
const reviews=calls.filter(call=>call.path.endsWith('/review-observation')).map(call=>call.request.body);
assert.deepEqual(reviews.map(item=>[item.action,item.active_ms,item.outcome]),
 [['APPROVE',2300,'SUCCESS'],['APPLY',0,'SUCCESS']]);
''',
    "failed_approval_retry_counts_only_new_review_time": r'''
nodes.get('change-detail').append(nodes.get('change-detail-title'));
nodes.get('change-detail-title').focus();window.dispatchEvent(new CustomEvent('focus'));
approveError=new Error('REQUEST_FAILED');reviewClock=2300;
await button('Approve exact proposal').emit('click');
approveError=null;reviewClock=3000;
await button('Approve exact proposal').emit('click');
const reviews=calls.filter(call=>call.path.endsWith('/review-observation')).map(call=>call.request.body);
assert.deepEqual(reviews.map(item=>[item.action,item.active_ms,item.outcome]),
 [['APPROVE',2300,'ERROR'],['APPROVE',700,'SUCCESS'],['APPLY',0,'SUCCESS']]);
''',
    "automatic_apply_failure_retains_zero_additional_review": r'''
nodes.get('change-detail').append(nodes.get('change-detail-title'));
nodes.get('change-detail-title').focus();window.dispatchEvent(new CustomEvent('focus'));
reviewClock=2300;applyError=new Error('RESPONSE_LOST');
await button('Approve exact proposal').emit('click');
const reviews=calls.filter(call=>call.path.endsWith('/review-observation')).map(call=>call.request.body);
assert.deepEqual(reviews.map(item=>[item.action,item.active_ms,item.outcome]),
 [['APPROVE',2300,'SUCCESS'],['APPLY',0,'ERROR']]);
assert.equal(commands().filter(call=>call.path.includes('/apply/')).length,1);
''',
    "runtime_drift_requires_new_proposal_without_apply_or_retry": r'''
approveError=Object.assign(new Error('WORKSPACE_RUNTIME_REPLAN_REQUIRED'),{code:'WORKSPACE_RUNTIME_REPLAN_REQUIRED'});
await button('Approve exact proposal').emit('click');
assert.equal(commands().length,1);
assert.equal(item.status,'PREVIEWED');
assert(nodes.get('change-notice').textContent.includes('revise as a new proposal'));
assert(!nodes.get('change-notice').textContent.includes('expired'));
assert.equal(commands().filter(call=>call.path.includes('/apply/')).length,0);
''',
    "approve_then_fresh_permission_then_exact_apply": r'''
const disclosure=nodes.get('change-detail-body').children.map(n=>n.textContent).join(' ');
assert(disclosure.includes('applied automatically if your account has execution permission'));
await button('Approve exact proposal').emit('click');
assert.deepEqual(commands().map(call=>call.path),['/api/workspace/approve/change%3Aone','/api/workspace/apply/change%3Aone']);
const approveIndex=calls.findIndex(call=>call.path.includes('/approve/'));
const applyIndex=calls.findIndex(call=>call.path.includes('/apply/'));
assert(calls.slice(approveIndex+1,applyIndex).some(call=>call.path==='/api/workspace/changes/change%3Aone'&&!call.request.method));
assert.equal(item.status,'APPLIED');assert(nodes.get('change-notice').textContent.includes('selective Rebase completed'));
await window.OrgRebaseChangeWorkbench.refresh();assert.equal(commands().length,2,'reads never start an apply loop');
''',
    "approval_failure_never_applies": r'''
approveError=Object.assign(new Error('REVIEW_GATE_PENDING'),{code:'REVIEW_GATE_PENDING'});
await button('Approve exact proposal').emit('click');
assert.equal(commands().length,1);assert.equal(item.status,'PREVIEWED');
assert.equal(button('Approve exact proposal').disabled,false);
''',
    "executor_permission_required": r'''
allowApply=false;await button('Approve exact proposal').emit('click');
assert.equal(commands().length,1);assert.equal(item.status,'APPROVED');
assert(nodes.get('change-notice').textContent.includes('awaiting an account with execution permission'));
''',
    "application_failure_keeps_approval_and_only_apply_is_retried": r'''
applyError=Object.assign(new Error('APPLY_UNCONFIRMED'),{code:'APPLY_UNCONFIRMED'});
await button('Approve exact proposal').emit('click');
assert.equal(commands().length,2);assert.equal(item.status,'APPROVED');
assert(nodes.get('change-notice').textContent.includes('Approval is recorded; application is not confirmed'));
assert.equal(button('Apply approved proposal').disabled,false);
assert(!calls.some(call=>call.path.endsWith('/review-observation')&&call.request.body.action==='APPROVE'&&call.request.body.outcome==='ERROR'));
applyError=null;await button('Apply approved proposal').emit('click');
assert.equal(item.status,'APPLIED');assert.equal(commands().filter(call=>call.path.includes('/approve/')).length,1);
assert.equal(commands().filter(call=>call.path.includes('/apply/')).length,2);
''',
    "busy_covers_approval_and_continuation": r'''
let release;approvalWait=new Promise(resolve=>release=resolve);
const approve=button('Approve exact proposal');const pending=approve.emit('click');await tick();
await approve.emit('click');assert.equal(commands().length,1);
assert.equal(nodes.get('change-workbench').getAttribute('aria-busy'),'true');
release();await pending;assert.equal(commands().length,2);assert.equal(item.status,'APPLIED');
''',
    "committed_change_recovers_record_without_new_approval": r'''
item.status='RECOVERY_REQUIRED';item.allowed_actions=['APPLY'];item.approval={approval_digest:'approval:one'};
await window.OrgRebaseChangeWorkbench.refresh();
assert(nodes.get('change-detail-title').textContent.includes('Applied; result recovery required'));
assert(nodes.get('change-detail-body').children.map(n=>n.textContent).join(' ').includes('without another approval or proposal'));
await button('Recover result record').emit('click');
assert.equal(commands().length,1);assert(commands()[0].path.includes('/apply/'));
assert.equal(item.status,'APPLIED');
assert(nodes.get('change-notice').textContent.includes('business change was not repeated'));
''',
    "direct_apply_failure_refreshes_committed_recovery_state": r'''
item.status='APPROVED';item.allowed_actions=['APPLY'];item.approval={approval_digest:'approval:one'};
await window.OrgRebaseChangeWorkbench.refresh();
applyObserved=()=>{item.status='RECOVERY_REQUIRED';};
applyError=Object.assign(new Error('RECORD_WRITE_FAILED'),{code:'RECORD_WRITE_FAILED'});
await button('Apply approved proposal').emit('click');
assert.equal(commands().length,1,'reading recovery status must not retry the write');
assert.equal(button('Recover result record').disabled,false);
assert(nodes.get('change-notice').textContent.includes('do not propose or approve again'));
applyError=null;await button('Recover result record').emit('click');
assert.equal(commands().length,2);assert.equal(item.status,'APPLIED');
assert.equal(commands().filter(call=>call.path.includes('/approve/')).length,0);
''',
    "lost_apply_response_reconciles_completed_result_without_retry": r'''
item.status='APPROVED';item.allowed_actions=['APPLY'];item.approval={approval_digest:'approval:one'};
await window.OrgRebaseChangeWorkbench.refresh();
applyObserved=()=>{item.status='APPLIED';item.allowed_actions=[];};
applyError=Object.assign(new Error('RESPONSE_LOST'),{code:'RESPONSE_LOST'});
await button('Apply approved proposal').emit('click');
assert.equal(commands().length,1);
assert(nodes.get('change-notice').textContent.includes('Current records confirm'));
''',
    "recovery_is_visible_without_executor_action": r'''
item.status='RECOVERY_REQUIRED';item.allowed_actions=[];item.approval={approval_digest:'approval:one'};
await window.OrgRebaseChangeWorkbench.refresh();
assert(nodes.get('change-detail-title').textContent.includes('result recovery required'));
assert.equal(nodes.get('change-detail-body').querySelectorAll('button').length,0);
assert.equal(commands().length,0);
''',
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_approval_continuation(scenario):
    run_node(HARNESS + "\n(async()=>{await ready();\n" + SCENARIOS[scenario]
             + "\n})().catch(error=>{console.error(error);process.exitCode=1;});")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("boundary", ["approval_response", "fresh_projection"])
@pytest.mark.parametrize("scope", ["workspace", "session", "run"])
def test_scope_change_during_approval_never_applies(boundary, scope):
    run_node(HARNESS + r'''
(async()=>{
 await ready();let release;const waiting=new Promise(resolve=>release=resolve);
 if(BOUNDARY==='approval_response')approvalWait=waiting;else detailWait=waiting;
 const pending=button('Approve exact proposal').emit('click');await tick();await tick();
 if(SCOPE==='workspace')workspace='workspace:two';
 else if(SCOPE==='session')session={mode:'local',principal:null};
 else window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:'run:two'}}}));
 release();await pending;
 assert.equal(commands().filter(call=>call.path.includes('/apply/')).length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("BOUNDARY", json.dumps(boundary)).replace("SCOPE", json.dumps(scope)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("scope", ["workspace", "session", "run"])
def test_scope_change_during_direct_apply_does_not_refresh_other_workspace(scope):
    run_node(HARNESS + r'''
(async()=>{
 await ready();item.status='APPROVED';item.allowed_actions=['APPLY'];item.approval={approval_digest:'approval:one'};
 await window.OrgRebaseChangeWorkbench.refresh();
 let release;applyWait=new Promise(resolve=>release=resolve);
 applyError=Object.assign(new Error('RESPONSE_LOST'),{code:'RESPONSE_LOST'});
 const pending=button('Apply approved proposal').emit('click');await tick();
 if(SCOPE==='workspace')workspace='workspace:two';
 else if(SCOPE==='session')session={mode:'local',principal:null};
 else window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:'run:two'}}}));
 const before=calls.length;release();await pending;
 assert.equal(calls.length,before,'late Apply error must not read or write the replacement context');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("SCOPE", json.dumps(scope)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("mismatch", ["run", "event", "event_digest", "preview", "approval"])
def test_approval_continuation_requires_exact_fresh_projection(mismatch):
    run_node(HARNESS + r'''
(async()=>{
 await ready();
 alterDetail=value=>{
  if(value.status!=='APPROVED')return value;
  if(MISMATCH==='run')value.execution_run_id='run:other';
  if(MISMATCH==='event')value.event.event_id='event:other';
  if(MISMATCH==='event_digest')value.event.digest='event:different';
  if(MISMATCH==='preview')value.preview.preview_digest='preview:different';
  if(MISMATCH==='approval')value.approval.approval_digest='approval:different';
  return value;
 };
 await button('Approve exact proposal').emit('click');
 assert.equal(commands().filter(call=>call.path.includes('/apply/')).length,0);
 assert.equal(item.status,'APPROVED');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("MISMATCH", json.dumps(mismatch)))
