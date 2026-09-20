"""Private task text must not outlive its authenticated workspace context."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const nodes=new Map(),queue=[],handlers={},requests=[];
let workspace='workspace:A',actor='actor:A';
const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},value:'',textContent:'',hidden:false,
 setAttribute(){},removeAttribute(){}});return nodes.get(id);};
const receipt={run_id:'run:same',digest:'receipt',actor_id:'actor:A',prompt_digest:'prompt',prompt_length:28,
 candidate_digest:'candidate',approval_digest:'approval',formation_receipt_digest:'formation'};
const state={stage:'CURRENT',execution:{run_id:receipt.run_id},task_intake:receipt,
 enterprise_data_lineage:{source:{task:{actor_id:'actor:A'}}}};
function payload(text){return {...receipt,schema_version:'orgrebase.workspace-task-intake-work-description-record.v1',
 status:'PRIVATE_RECORD_RETAINED',semantic_use:'TASK_INTENT_ONLY',contributes_business_facts:false,
 grants_authority:false,included_in_public_evidence:false,included_in_events_or_otlp:false,work_description:text};}
const context=vm.createContext({window:{OrgRebaseClient:{workspace:()=>workspace,
 session:()=>({principal:{actor_id:actor}}),json(){return new Promise((resolve,reject)=>requests.push({resolve,reject,actor,workspace}));}},
 setTimeout(fn){queue.push(fn);},addEventListener(name,fn){handlers[name.split(':').at(-1)]=fn;}},
 document:{querySelector:()=>null},STAGES:['EMPTY','CURRENT'],ROUTES:[],language:'zh-CN',currentState:state,stateAvailability:'ready',
 taskWorkDescription:null,taskWorkDescriptionRunId:null,taskWorkDescriptionStatus:'idle',
 taskWorkDescriptionContext:null,taskWorkDescriptionGeneration:0,taskCandidate:null,taskApproval:null,
 taskIntakeBusy:false,taskIntakeAction:'idle',taskIntakeError:null,byId:node,
 copy:()=>new Proxy({},{get:(_,k)=>k}),persistedTaskIntake:s=>s.task_intake,
 taskScope:s=>({actorId:s?.enterprise_data_lineage?.source?.task?.actor_id}),taskIntakeHeaders:()=>({}),
 organizationContractReady:()=>true,organizationContractEstablished:()=>true,displayTaskActor:x=>x,
 displayTaskCustomer:x=>x,format:x=>x,taskIntakeIssues:()=>[],setExactText(n,v){n.textContent=v;},
 setFormedWorkspaceVisibility(){},renderRunProgress(){},renderContextFacts(){},renderElementObservation(){},
 renderOverviewFlow(){},renderMaterials(){},renderTaskPrerequisite(){},renderLifecycleRail(){}});
const take=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
vm.runInContext(take('  function taskWorkDescriptionKey(','  function setFormedWorkspaceVisibility(')
 +take('  function renderTaskIntake(','  async function prepareTaskIntake(')
 +take('  function renderState(','  function boot('),context);
for(const name of ['stateunavailable','sessionended']){
 const start=source.indexOf(`    window.addEventListener("orgrebase:${name}"`);
 vm.runInContext(source.slice(start,source.indexOf('    });',start)+7),context);
}
const tick=()=>new Promise(setImmediate);
async function begin(){context.renderState(state);queue.shift()();await tick();}
async function complete(text){await begin();requests.at(-1).resolve(payload(text));await tick();}
const session=subject=>({mode:'oidc',authentication_required:true,authenticated:true,
 principal:{issuer:'issuer',subject,tenant_id:'tenant',actor_id:subject}});
const reasons=[];let catalogs=0;
function bindIdentityBridge(initial=session('actor:A')){
 const app=fs.readFileSync('demo/console/app.js','utf8');
 const part=(a,b)=>app.slice(app.indexOf(a),app.indexOf(b,app.indexOf(a)));
 vm.runInContext(part('function sessionIdentity(', '\nasync function refreshState('),context);
 context.workspaceSessionIdentity=initial?context.sessionIdentity(initial):null;
 context.workspaceSessionRevision=0;context.workspaceStateRequest=null;context.currentSkillCatalog={status:'PASS'};
 context.invalidateSkillSource=()=>{context.currentSkillCatalog={status:'UNAVAILABLE',packages:[]};};
 context.loadSkillSourceCatalog=()=>{catalogs++;context.currentSkillCatalog={status:'PASS'};};
 context.renderWorkspaceUnavailable=options=>{reasons.push(options.reason);handlers.stateunavailable({detail:{reason:options.reason}});};
 vm.runInContext(part('function invalidateWorkspaceState(', '\nfunction refreshChangedWorkspace('),context);
 const start=app.indexOf('window.addEventListener("orgrebase:sessionchange"');
 vm.runInContext(app.slice(start,app.indexOf('\n});',start)+4),context);
}
function draft(){context.currentState={...state,stage:'EMPTY',task_intake:null};
 node('task-request-prompt').value='Unsubmitted private A request';
 context.taskCandidate={digest:'candidate:A'};context.taskApproval={digest:'approval:A'};}
'''


def test_cached_private_text_is_cleared_when_identity_changes_for_the_same_run():
    run_node(HARNESS + r'''
(async()=>{
 await complete('Private A text');node('task-request-prompt').value='Submitted A text';
 node('task-compass-demand').textContent='Private A text';
 actor='actor:B';handlers.stateunavailable();
 assert.equal(node('task-intake-private').hidden,true);
 assert.equal(node('task-intake-private-text').textContent,'');
 assert.equal(node('task-compass-demand').textContent,'');
 assert.equal(node('task-request-prompt').value,'','submitted text is not an unsent draft');
 await begin();assert.equal(requests.length,2,'same run requires fresh server authorization');
 assert.notEqual(node('task-intake-private-text').textContent,'Private A text');
 requests[1].reject(Object.assign(new Error('ACTOR_DENIED'),{status:403}));await tick();
 assert.equal(context.taskWorkDescription,null);assert.equal(context.taskWorkDescriptionStatus,'unavailable');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_same_actor_in_another_workspace_cannot_reuse_private_text():
    run_node(HARNESS + r'''
(async()=>{
 await complete('Private workspace A');workspace='workspace:B';await begin();
 assert.equal(requests.length,2);assert.equal(requests[1].workspace,'workspace:B');
 assert.equal(context.taskWorkDescription,null);
 requests[1].resolve(payload('Private workspace B'));await tick();
 assert.equal(node('task-intake-private-text').textContent,'Private workspace B');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_late_private_response_is_discarded_after_invalidating_the_same_context(outcome):
    run_node(HARNESS + r'''
(async()=>{
 await begin();handlers.stateunavailable();await begin();
 assert.equal(requests.length,2);
 requests[1].resolve(payload('Fresh authorized text'));await tick();
 if(OUTCOME==='success')requests[0].resolve(payload('Stale private text'));
 else requests[0].reject(Object.assign(new Error('NOT_FOUND'),{status:404}));
 await tick();assert.equal(context.taskWorkDescription,'Fresh authorized text');
 assert.equal(context.taskWorkDescriptionStatus,'available');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("OUTCOME", repr(outcome)))


def test_same_identity_context_reuses_cache_but_invalidated_timers_do_not_read():
    run_node(HARNESS + r'''
(async()=>{
 await complete('Private text');context.renderState(state);context.renderState(state);
 assert.equal(requests.length,1);assert.equal(queue.length,0);
 handlers.stateunavailable();context.renderState(state);assert.equal(queue.length,1);
 handlers.stateunavailable();queue.shift()();await tick();
 assert.equal(requests.length,1,'an invalidated scheduled read must not start');
 context.currentState={...state,stage:'EMPTY',task_intake:null};
 node('task-request-prompt').value='Unsubmitted draft';handlers.stateunavailable();
 assert.equal(node('task-request-prompt').value,'Unsubmitted draft','ordinary drafts survive read failures');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


@pytest.mark.parametrize("next_actor", ["actor:A", "actor:B"])
def test_identity_anchor_preserves_own_draft_after_expiry_but_discards_it_for_another_subject(next_actor):
    run_node(HARNESS + r'''
bindIdentityBridge();draft();const candidate=context.taskCandidate,approval=context.taskApproval;
handlers.sessionchange({detail:null});
assert.equal(node('task-request-prompt').value,'Unsubmitted private A request');
assert.equal(context.taskCandidate,candidate);assert.equal(context.taskApproval,approval);
assert.equal(context.currentSkillCatalog.status,'UNAVAILABLE');
handlers.sessionchange({detail:session(NEXT_ACTOR)});
assert.equal(context.currentSkillCatalog.status,'PASS','same identity reauthentication restores the invalidated Skill catalog');
assert.equal(catalogs,1);
if(NEXT_ACTOR==='actor:A'){
 assert.equal(node('task-request-prompt').value,'Unsubmitted private A request');
 assert.equal(context.taskCandidate,candidate);assert.equal(context.taskApproval,approval);
 assert(!reasons.includes('identity-changed'));
}else{
 assert.equal(node('task-request-prompt').value,'');assert.equal(context.taskCandidate,null);
 assert.equal(context.taskApproval,null);assert(reasons.includes('identity-changed'));
}
'''.replace("NEXT_ACTOR", repr(next_actor)))


def test_direct_identity_change_and_explicit_logout_discard_drafts_while_local_session_initializes_once():
    run_node(HARNESS + r'''
bindIdentityBridge();draft();handlers.sessionchange({detail:session('actor:B')});
assert.equal(node('task-request-prompt').value,'');assert.equal(context.taskCandidate,null);assert.equal(context.taskApproval,null);
draft();handlers.sessionended({detail:{reason:'logout'}});
assert.equal(node('task-request-prompt').value,'');assert.equal(context.taskCandidate,null);assert.equal(context.taskApproval,null);
draft();handlers.sessionended({detail:{reason:'role-switch'}});
assert.equal(node('task-request-prompt').value,'');assert.equal(context.taskCandidate,null);assert.equal(context.taskApproval,null);
bindIdentityBridge(null);catalogs=0;reasons.length=0;
const local={mode:'local',authentication_required:false,authenticated:false,principal:null};
handlers.sessionchange({detail:local});handlers.sessionchange({detail:{...local}});
assert.equal(catalogs,1);assert.equal(reasons.length,0,'local mode is an available identity without a login loop');
''')
