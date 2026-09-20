"""Change task rendering and read-only progress polling, with a controlled clock."""

from __future__ import annotations

import json
import shutil

import pytest

from orgrebase.workspace.change_agentteams import progress_summary
from tests.workspace.test_workspace_client import run_node


def native_projection():
    """Use the production public projection; these are synthetic UI test records."""
    task = {"logical_task_id": "logical:product", "task_id": "task:product", "actor_id": "product",
            "kind": "CANDIDATE", "delegation_digest": "delegation:one", "depends_on": []}
    review = {**task, "logical_task_id": "logical:review", "task_id": "task:review",
              "actor_id": "reviewer", "kind": "DETERMINISTIC_CONTRACT_REVIEW"}
    actions = [{"sequence": 1, "key": "project:plan", "tool": "projectflow", "action": "plan_dag",
                "status": "planned", "ok": True, "digest": "action:plan"}]
    for suffix, name in (("delegate", "delegate_task"), ("ack", "ack_task"),
                         ("submit", "submit_task"), ("check", "check_task"),
                         ("accept", "accept_task_result")):
        actions.append({"sequence": len(actions) + 1, "key": f"task:product:{suffix}", "tool": "taskflow",
                        "action": name, "status": "done", "ok": True, "digest": f"action:{name}"})
    return progress_summary({
        "status": "COMPLETED", "digest": "receipt:one", "project_id": "project:one",
        "binding": {"tenant_id": "tenant:one", "workspace_id": "workspace:one", "attempt_key": "attempt:one",
                    "request_digest": "request:one", "run_id": "run:one", "nonce": "nonce:one",
                    "change_set_ref": "changeset:workspace-change:one@1", "change_set_digest": "change-set:one",
                    "preview_digest": "preview:one", "revision_lock_digest": "lock:one", "plan_digest": "plan:one"},
        "native_agentteams_observed": True, "evidence_class": "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
        "tasks": [task, review], "actions": actions, "results": [],
        "raw_actions": [{"response": "PRIVATE_MCP_RESPONSE"}],
    })


HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require('./tests/workspace/console_dom_harness.js');
const listeners=new Map(),timers=new Map(),calls=[],polls=[];
let clock=0,timerId=0,session={mode:'local',principal:null},workspace='workspace:one';
let holdPolls=false,honorAbort=true,previewWait=null,observationWait=null,previewError=null;
window.addEventListener=(type,fn)=>{if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);};
window.removeEventListener=(type,fn)=>listeners.get(type)?.delete(fn);
window.dispatchEvent=event=>{for(const fn of [...listeners.get(event.type)||[]])fn(event);};
const listenerCount=()=>[...listeners.values()].reduce((sum,set)=>sum+set.size,0);
const setTimer=(fn,delay)=>{const id=++timerId;timers.set(id,{fn,at:clock+delay});return id;};
const clearTimer=id=>timers.delete(id);
const flush=async()=>{await tick();await tick();};
async function advance(ms){clock+=ms;for(const [id,timer] of [...timers]){
 if(timer.at<=clock&&timers.delete(id))timer.fn();
}await flush();}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const text=node=>node?[node.textContent||'',...node.children.map(text)].join(' '):'';
const body=()=>nodes.get('change-detail-body');
const nativeNode=()=>{const node=nodes.get('change-agentteams-tasks');return node&&body().contains(node)?node:null;};
const item={execution_run_id:'run:one',event:{event_id:'change:one',digest:'event:one',slot_id:'product_plan',
 owner_id:'owner:one',base_version:'v1',base_digest:'base:one',proposal:{payload:{canonical_value:'New plan'},source_refs:['source:one']}},
 status:'RECEIVED',allowed_actions:['PREVIEW'],preview:null};
const configuration={execution_run_id:'run:one',fields:[]};
const native=NATIVE_PROJECTION;
function pendingNative(){item.advisory_attempt={state:'IN_PROGRESS',deadline_epoch_ms:999999,usage_status:'UNKNOWN',
 receipt_summaries:[],native_execution:structuredClone(native)};}
function completeNative(){pendingNative();item.status='PREVIEWED';item.allowed_actions=[];item.advisory_attempt.state='COMPLETE';
 item.preview={preview_digest:'preview:one',native_execution:structuredClone(native),bundle:{
 change_set:{id:'changeset:workspace-change:one',revision:1,digest:'change-set:one'},
 preview:{digest:'preview:one'},run_envelope:{run_id:'run:one',nonce:'nonce:one'},
 advisory:{orchestration_plan:{digest:'plan:one'}}}};
}
window.OrgRebaseClient={session:()=>session,workspace:()=>workspace,async json(path,request={}){
 calls.push({path,request});
 if(path==='/api/workspace/change-options')return structuredClone(configuration);
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};
 if(path.endsWith('/review-observation')){if(observationWait)await observationWait.promise;return {};}
 if(path.includes('/preview/')){if(previewWait)await previewWait.promise;if(previewError)throw previewError;return {};}
 if(request.signal){
  const pending=deferred();polls.push({signal:request.signal,...pending});
  if(honorAbort)request.signal.addEventListener('abort',()=>pending.reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true});
  if(!holdPolls)pending.resolve(structuredClone(item));
  return await pending.promise;
 }
 return structuredClone(item);
}};
vm.runInNewContext(fs.readFileSync('demo/console/change-workbench.js','utf8'),
 {window,document,CustomEvent,performance,AbortController,setTimeout:setTimer,clearTimeout:clearTimer,
 crypto:{randomUUID:()=>String(calls.length)}});
const commands=()=>calls.filter(call=>call.request.method==='POST');
const previewCommands=()=>commands().filter(call=>call.path.includes('/preview/'));
async function ready(){await flush();window.dispatchEvent(new CustomEvent('orgrebase:staterendered',
 {detail:{execution:{run_id:'run:one'}}}));await flush();}
async function beginPreview(){previewWait=deferred();const pending=button('Check change impact').emit('click');await flush();
 assert.equal(previewCommands().length,1);return {pending};}
async function finishPreview(pending){completeNative();previewWait.resolve();await pending;await flush();}
'''


def run_scenario(script):
    run_node(HARNESS.replace("NATIVE_PROJECTION", json.dumps(native_projection()))
             + "\n(async()=>{await ready();\n" + script
             + "\n})().catch(error=>{console.error(error);process.exitCode=1;});")


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")


def test_public_receipt_displays_task_phases_without_raw_response_or_approval_claim():
    run_scenario(r'''
completeNative();await window.OrgRebaseChangeWorkbench.refresh();
assert(nativeNode());assert(text(nativeNode()).includes('product · Acceptance'));
assert(text(nativeNode()).includes('Independent contract review · Awaiting delegation'));
assert(text(nativeNode()).includes('human approval and application remain separate'));
assert(text(nativeNode()).includes('receipt:one'));assert(!text(body()).includes('PRIVATE_MCP_RESPONSE'));
// Even an unexpected extra field is not serialized into the public task panel.
item.preview.native_execution.raw_actions=[{response:'PRIVATE_MCP_RESPONSE'}];
const phases=['Acceptance','AT check','Submission','Acknowledgement','Delegation'];
for(const phase of phases){await window.OrgRebaseChangeWorkbench.refresh();
 assert(text(nativeNode()).includes('product · '+phase));
 assert(!text(body()).includes('PRIVATE_MCP_RESPONSE'));
 item.preview.native_execution.actions.pop();
}
await window.OrgRebaseChangeWorkbench.refresh();assert(text(nativeNode()).includes('product · Awaiting delegation'));
item.preview.native_execution.actions=[];await window.OrgRebaseChangeWorkbench.refresh();
assert(text(nativeNode()).includes('Creating tasks for this change.'));
assert(!text(nativeNode()).includes('product ·'));
assert.equal(commands().length,0);
''')


def test_shared_attempt_panel_does_not_borrow_selected_changes_tasks():
    run_scenario(r'''
completeNative();await window.OrgRebaseChangeWorkbench.refresh();assert(nativeNode());
const groupPanel=document.createElement('section');
window.OrgRebaseChangeWorkbench.renderAdvisoryAttempt(groupPanel,
 {state:'IN_PROGRESS',usage_status:'UNKNOWN',receipt_summaries:[]},'source-group-advisory-attempt');
assert(text(groupPanel).includes('Candidate generation in progress'));
assert(!text(groupPanel).includes('AgentTeams tasks for this change'));
assert(!text(groupPanel).includes('project:one'));
''')


@pytest.mark.parametrize("field", ["run_id", "change_set_ref", "change_set_digest", "preview_digest", "plan_digest", "nonce"])
def test_completed_receipt_from_other_change_or_preview_is_not_displayed(field):
    run_scenario(r'''
completeNative();item.preview.native_execution.binding[FIELD]='other';
await window.OrgRebaseChangeWorkbench.refresh();assert.equal(nativeNode(),null);
assert.equal(commands().length,0);
'''.replace("FIELD", json.dumps(field)))


@pytest.mark.parametrize("field,value", [("candidate_only", False), ("target_writes", 1)])
def test_receipt_with_business_write_claim_is_not_displayed(field, value):
    run_scenario(r'''
completeNative();item.preview.native_execution[FIELD]=VALUE;
await window.OrgRebaseChangeWorkbench.refresh();assert.equal(nativeNode(),null);
'''.replace("FIELD", json.dumps(field)).replace("VALUE", json.dumps(value)))


def test_pending_preview_reads_progress_without_reposting_and_cleans_up():
    run_scenario(r'''
const baseline=listenerCount();const {pending}=await beginPreview();
pendingNative();await advance(1000);assert(nativeNode());assert.equal(polls.length,1);
assert.equal(commands().length,1);assert.equal(previewCommands().length,1);
// A second click cannot start another preview while the command is outstanding.
await button('Check change impact').emit('click');assert.equal(previewCommands().length,1);
holdPolls=true;await advance(2000);assert.equal(polls.length,2);
assert.equal(polls[1].signal.aborted,false);
await finishPreview(pending);
assert.equal(polls[1].signal.aborted,true);assert.equal(timers.size,0);
assert.equal(listenerCount(),baseline);assert.equal(previewCommands().length,1);
const count=calls.length;await advance(10000);assert.equal(calls.length,count);
''')


@pytest.mark.parametrize("mismatch", ["execution_run_id", "event_id", "event_digest"])
def test_poll_response_for_another_change_cannot_replace_current_detail(mismatch):
    run_scenario(r'''
holdPolls=true;const {pending}=await beginPreview();await advance(1000);
pendingNative();const response=structuredClone(item);response.event.proposal.payload.canonical_value='FOREIGN_PROPOSAL';
if(MISMATCH==='execution_run_id')response.execution_run_id='other';
if(MISMATCH==='event_id')response.event.event_id='other';
if(MISMATCH==='event_digest')response.event.digest='other';
polls[0].resolve(response);await flush();assert(!text(body()).includes('FOREIGN_PROPOSAL'));
assert.equal(nativeNode(),null);await finishPreview(pending);
'''.replace("MISMATCH", json.dumps(mismatch)))


@pytest.mark.parametrize("scope", ["workspace", "session", "run"])
@pytest.mark.parametrize("outcome", ["success", "error"])
def test_context_switch_aborts_poll_and_discards_late_poll_and_command(scope, outcome):
    run_scenario(r'''
holdPolls=true;honorAbort=false;const baseline=listenerCount();
const {pending}=await beginPreview();await advance(1000);
const late=structuredClone(item);late.event.proposal.payload.canonical_value='STALE_PROPOSAL';
if(SCOPE==='workspace'){workspace='workspace:two';
 window.dispatchEvent(new CustomEvent('orgrebase:workspacechange',{detail:{source:'change-workbench'}}));}
if(SCOPE==='session'){session={mode:'local',principal:null};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));}
if(SCOPE==='run')window.dispatchEvent(new CustomEvent('orgrebase:staterendered',
 {detail:{execution:{run_id:'run:two'}}}));
assert.equal(polls[0].signal.aborted,true);assert.equal(listenerCount(),baseline);
const count=calls.length,notice=nodes.get('change-notice').textContent;
polls[0].resolve(late);await flush();
if(OUTCOME==='error')previewError=Object.assign(new Error('OLD_PREVIEW_FAILED'),{code:'OLD_PREVIEW_FAILED'});
previewWait.resolve();await pending;await flush();
assert(!text(body()).includes('STALE_PROPOSAL'));
assert.equal(nodes.get('change-notice').textContent,notice);
assert.equal(calls.length,count,'late completion must not read/write replacement context');
assert.equal(timers.size,0);assert.equal(previewCommands().length,1);
'''.replace("SCOPE", json.dumps(scope)).replace("OUTCOME", json.dumps(outcome)))


@pytest.mark.parametrize("outcome", ["success", "error"])
def test_context_switch_during_observation_cannot_display_stale_preview_result(outcome):
    run_scenario(r'''
completeNative();item.allowed_actions=['PREVIEW'];await window.OrgRebaseChangeWorkbench.refresh();
observationWait=deferred();const {pending}=await beginPreview();
if(OUTCOME==='error')previewError=Object.assign(new Error('OLD_PREVIEW_FAILED'),{code:'OLD_PREVIEW_FAILED'});
previewWait.resolve();await flush();
assert(calls.some(call=>call.path.endsWith('/review-observation')));
workspace='workspace:two';window.dispatchEvent(new CustomEvent('orgrebase:workspacechange',
 {detail:{source:'change-workbench'}}));
const count=calls.length,notice=nodes.get('change-notice').textContent;
observationWait.resolve();await pending;await flush();
assert.equal(calls.length,count);assert.equal(nodes.get('change-notice').textContent,notice);
assert.equal(timers.size,0);assert.equal(previewCommands().length,1);
'''.replace("OUTCOME", json.dumps(outcome)))


def test_poll_timeout_recovers_on_next_read_without_repeating_preview():
    run_scenario(r'''
holdPolls=true;const {pending}=await beginPreview();await advance(1000);
assert.equal(polls.length,1);await advance(5000);assert.equal(polls[0].signal.aborted,true);
assert.equal(polls.length,1);await advance(1999);assert.equal(polls.length,1);
pendingNative();holdPolls=false;await advance(1);assert.equal(polls.length,2);assert(nativeNode());
assert.equal(commands().length,1);await finishPreview(pending);assert.equal(timers.size,0);
''')


def test_hidden_page_does_not_poll_and_pagehide_aborts_inflight_read():
    run_scenario(r'''
const baseline=listenerCount();const {pending}=await beginPreview();
document.visibilityState='hidden';await advance(1000);assert.equal(polls.length,0);
document.visibilityState='visible';holdPolls=true;await advance(2000);assert.equal(polls.length,1);
window.dispatchEvent(new CustomEvent('pagehide'));await flush();
assert.equal(polls[0].signal.aborted,true);assert.equal(timers.size,0);assert.equal(listenerCount(),baseline);
const count=polls.length;await advance(10000);assert.equal(polls.length,count);
await finishPreview(pending);assert.equal(timers.size,0);
''')


def test_recovery_task_heading_distinguishes_retained_and_current_receipts():
    run_scenario(r'''
completeNative();item.recovery={state:'NEEDS_EVIDENCE',preserved_context:{previous_native_receipt_digest:'receipt:one'}};
await window.OrgRebaseChangeWorkbench.refresh();assert(text(nativeNode()).includes('task records before the evidence request'));
item.recovery.state='RESUMING';item.recovery.preserved_context.previous_native_receipt_digest='receipt:prior';
await window.OrgRebaseChangeWorkbench.refresh();assert(text(nativeNode()).includes('AgentTeams tasks for this change'));
assert(!text(nativeNode()).includes('task records before the evidence request'));
assert.equal(commands().length,0);
''')
