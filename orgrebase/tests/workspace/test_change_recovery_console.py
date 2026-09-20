"""Exercise the evidence continuation forms and workspace command boundaries."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require('./tests/workspace/console_dom_harness.js');
const calls=[];let uuid=0,postError=null,holdPost=null,afterPost=null;
const listeners=new Map();
window.addEventListener=(type,fn)=>{if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);};
window.removeEventListener=(type,fn)=>listeners.get(type)?.delete(fn);
window.dispatchEvent=event=>{for(const fn of [...listeners.get(event.type)||[]])fn(event);};
let session={mode:'local',principal:{actor_id:'finance-owner'}},workspace='workspace:one';
const item={execution_run_id:'run:one',event:{event_id:'change:one',digest:'event:one',slot_id:'pricing_policy',
 owner_id:'finance-owner',base_version:'v1',base_digest:'base:one',proposal:{payload:{canonical_value:{discount_bps:1000,tax_bps:2000}},source_refs:['source:one']}},
 status:'PREVIEWED',allowed_actions:[],preview:null,recovery:{state:'NONE',round:0,context_digest:'context:one',recovery_digest:null,
 owner_id:'finance-owner',requested_by:null,reason:null,task_id:null,required_evidence_refs:[],
 tasks:[{task_id:'task:finance',actor_id:'finance-agent',domain_id:'finance',evidence_refs:['fact:finance@v1'],allowed_executors:[
 {executor_id:'finance-primary',label:'财务执行',label_en:'Finance executor',capability_ref:'finance'},
 {executor_id:'finance-alternate',label:'财务备用执行',label_en:'Alternate finance executor',capability_ref:'finance'}]},
 {task_id:'task:product',actor_id:'product-agent',domain_id:'product',evidence_refs:['fact:product@v1'],allowed_executors:[]}],
 evidence_options:[{ref:'fact:finance@v1',digest:'digest:finance',domain_id:'finance',label:'财务依据',label_en:'Finance evidence'},
 {ref:'fact:product@v1',digest:'digest:product',domain_id:'product',label:'产品依据',label_en:'Product evidence'}],
 preserved_context:{event_id:'change:one',change_set_digest:'change-set:one',preview_digest:'preview:one',
 previous_preview_artifact_digest:'old-preview',previous_native_receipt_digest:'old-tasks'},history:[],allowed_actions:['RETURN_FOR_EVIDENCE']}};
const configuration={execution_run_id:'run:one',fields:[]};
window.OrgRebaseClient={session:()=>session,workspace:()=>workspace,async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(request.method==='POST'){
  if(holdPost)await holdPost;
  afterPost?.();
  if(postError)throw postError;
  if(path.endsWith('/return-for-evidence')){Object.assign(item.recovery,{state:'NEEDS_EVIDENCE',round:1,recovery_digest:'recovery:one',
   task_id:request.body.task_id,reason:request.body.reason,required_evidence_refs:request.body.required_evidence_refs,
   requested_by:session.principal.actor_id,allowed_actions:[]});return structuredClone(item.recovery);}
  if(path.endsWith('/resume')){Object.assign(item.recovery,{state:'READY_FOR_REVIEW',recovery_digest:'recovery:two',allowed_actions:[]});return structuredClone(item.recovery);}
  if(path.includes('/approve/')){item.status='APPROVED';item.approval={approval_digest:'approval:one'};item.allowed_actions=[];return {approval_digest:'approval:one'};}
  return {};
 }
 if(path.endsWith('/change-options'))return structuredClone(configuration);
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};
 return structuredClone(item);
}};
const scope={window,document,CustomEvent,performance,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:()=>String(++uuid)}};
vm.runInNewContext(fs.readFileSync('demo/console/change-recovery.js','utf8'),scope);
vm.runInNewContext(fs.readFileSync('demo/console/change-workbench.js','utf8'),scope);
const flush=async()=>{await tick();await tick();};
const text=node=>node?[node.textContent||'',...node.children.map(text)].join(' '):'';
const controls=()=>nodes.get('change-recovery-evidence-list').children.map(label=>label.children[0]);
const commands=()=>calls.filter(c=>c.request.method==='POST');
async function chooseEvidence(ref='fact:finance@v1'){
 const input=controls().find(input=>input.value===ref);assert(input);input.checked=true;await input.emit('change');
}
async function returnDraft(){nodes.get('change-recovery-reason').value='Confirm current pricing authority';await nodes.get('change-recovery-reason').emit('input');await chooseEvidence();}
async function becomeProposer(){session={mode:'local',principal:{actor_id:'sales'}};
 Object.assign(item.recovery,{state:'NEEDS_EVIDENCE',round:1,recovery_digest:'recovery:one',task_id:'task:finance',required_evidence_refs:['fact:finance@v1'],allowed_actions:['RESUME']});
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'role-switch'}}));await window.OrgRebaseChangeWorkbench.refresh();}
async function ready(){await flush();window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{execution:{run_id:'run:one'}}}));await flush();}
'''

SCENARIOS = {
    "owner_requests_exact_task_and_real_evidence_without_approval": r'''
assert(nodes.get('change-recovery-submit').disabled);await returnDraft();
assert.equal(nodes.get('change-recovery-submit').disabled,false);
await nodes.get('change-recovery-form').emit('submit');
const posts=commands();assert.equal(posts.length,1);assert(posts[0].path.endsWith('/return-for-evidence'));
assert.equal(posts[0].request.body.expected_context_digest,'context:one');
assert.equal(posts[0].request.body.task_id,'task:finance');assert.deepEqual(posts[0].request.body.required_evidence_refs,['fact:finance@v1']);
assert.equal(nodes.get('change-evidence-recovery').dataset.state,'NEEDS_EVIDENCE');
assert(text(nodes.get('change-recovery-context')).includes('old-tasks'));
assert.equal(window.OrgRebaseChangeWorkbench.authorityProgress().steps.find(s=>s.key==='candidate').status,'blocked');
''',
    "language_switch_keeps_unsent_reason_and_checked_evidence": r'''
await returnDraft();assert(text(nodes.get('change-evidence-recovery')).includes('Finance evidence'));
document.documentElement.lang='zh-CN';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert(text(nodes.get('change-evidence-recovery')).includes('财务依据'));
assert.equal(nodes.get('change-recovery-reason').value,'Confirm current pricing authority');assert(controls()[0].checked);
document.documentElement.lang='en';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert(text(nodes.get('change-evidence-recovery')).includes('Finance evidence'));assert(!text(nodes.get('change-evidence-recovery')).includes('财务依据'));
assert(controls()[0].checked);assert.equal(commands().length,0);
''',
    "task_change_does_not_carry_evidence_from_another_domain": r'''
await returnDraft();nodes.get('change-recovery-task').value='task:product';await nodes.get('change-recovery-task').emit('change');
assert.deepEqual(controls().map(input=>input.value),['fact:product@v1']);assert(!controls()[0].checked);
assert(nodes.get('change-recovery-submit').disabled);await nodes.get('change-recovery-form').emit('submit');assert.equal(commands().length,0);
''',
    "proposer_supplies_exact_digest_and_admitted_replacement": r'''
await becomeProposer();assert(nodes.get('change-recovery-submit').disabled);await chooseEvidence();
nodes.get('change-recovery-executor').value='finance-alternate';await nodes.get('change-recovery-executor').emit('change');
await nodes.get('change-recovery-form').emit('submit');
assert.equal(commands().length,1);const request=commands()[0];assert(request.path.endsWith('/resume'));
assert.equal(request.request.body.executor_id,'finance-alternate');assert.equal(request.request.body.recovery_digest,'recovery:one');
assert.deepEqual(request.request.body.evidence,[{ref:'fact:finance@v1',digest:'digest:finance'}]);
assert.equal(nodes.get('change-evidence-recovery').dataset.state,'READY_FOR_REVIEW');
assert(nodes.get('change-recovery-next').textContent.includes('finance-owner'));
assert(!commands().some(c=>c.path.includes('/approve/')||c.path.includes('/apply/')));
''',
    "missing_required_source_and_unknown_executor_keep_resume_closed": r'''
await becomeProposer();item.recovery.required_evidence_refs.push('fact:missing');await window.OrgRebaseChangeWorkbench.refresh();
await chooseEvidence();assert(nodes.get('change-recovery-submit').disabled);assert(nodes.get('change-recovery-validation').textContent.includes('fact:missing'));
item.recovery.required_evidence_refs=['fact:finance@v1'];await window.OrgRebaseChangeWorkbench.refresh();
nodes.get('change-recovery-executor').value='admin-unregistered';await nodes.get('change-recovery-executor').emit('change');
await nodes.get('change-recovery-form').emit('submit');assert.equal(commands().length,0);
''',
    "network_retry_reuses_operation_id_and_never_retries_automatically": r'''
await returnDraft();postError=Object.assign(new Error('NETWORK'),{code:'NETWORK'});
await nodes.get('change-recovery-form').emit('submit');await flush();assert.equal(commands().length,1);
await nodes.get('change-recovery-form').emit('submit');assert.equal(commands().length,2);
assert.equal(commands()[0].request.body.operation_id,commands()[1].request.body.operation_id);
assert.equal(item.recovery.state,'NONE');
''',
    "failed_resume_uses_current_round_recovery_instructions": r'''
await becomeProposer();await chooseEvidence();
afterPost=()=>Object.assign(item.recovery,{state:'FAILED',execution_uncertain:false,allowed_actions:[]});
postError=Object.assign(new Error('WORKSPACE_ADVISORY_ATTEMPT_FAILED'),{code:'WORKSPACE_ADVISORY_ATTEMPT_FAILED'});
await nodes.get('change-recovery-form').emit('submit');
assert.equal(commands().length,1);
assert(nodes.get('change-notice').textContent.includes('new evidence round'));
assert(!nodes.get('change-notice').textContent.includes('new proposal'));
assert(nodes.get('change-recovery-next').textContent.includes('new evidence round'));
''',
    "unconfirmed_execution_never_instructs_user_to_repeat_dispatch": r'''
await becomeProposer();await chooseEvidence();
afterPost=()=>Object.assign(item.recovery,{state:'FAILED',execution_uncertain:true,allowed_actions:[]});
postError=Object.assign(new Error('WORKSPACE_ADVISORY_RESULT_UNKNOWN'),{code:'WORKSPACE_ADVISORY_RESULT_UNKNOWN'});
await nodes.get('change-recovery-form').emit('submit');
assert.equal(commands().length,1);
assert(nodes.get('change-notice').textContent.includes('cannot be delegated again'));
assert(nodes.get('change-recovery-next').textContent.includes('cannot be delegated again'));
assert(!nodes.get('change-evidence-recovery').contains(nodes.get('change-recovery-form')));
document.documentElement.lang='zh-CN';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert(nodes.get('change-recovery-next').textContent.includes('当前不能重新委派'));
assert.equal(commands().length,1);
''',
    "role_switch_during_post_discards_old_completion_and_draft": r'''
await returnDraft();let resolve;holdPost=new Promise(done=>resolve=done);
const pending=nodes.get('change-recovery-form').emit('submit');await flush();
session={mode:'local',principal:{actor_id:'sales'}};
window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'role-switch'}}));
postError=Object.assign(new Error('old failure'),{code:'OLD_ROLE_FAILURE'});resolve();await pending;holdPost=null;postError=null;
await window.OrgRebaseChangeWorkbench.refresh();assert.equal(nodes.get('change-recovery-reason').value,'');
assert(!controls().some(input=>input.checked));assert(!nodes.get('change-notice').textContent?.includes('OLD_ROLE_FAILURE'));
assert.equal(commands().length,1);
''',
    "expired_session_followed_by_another_login_does_not_inherit_evidence_draft": r'''
await returnDraft();window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'expired'}}));
session={mode:'authenticated',principal:{actor_id:'other-owner'}};
await window.OrgRebaseChangeWorkbench.refresh();
assert.equal(nodes.get('change-recovery-reason').value,'');assert(!controls().some(input=>input.checked));
assert.equal(commands().length,0);
''',
    "wrong_change_context_cannot_render_action_form": r'''
item.recovery.preserved_context.event_id='change:other';await window.OrgRebaseChangeWorkbench.refresh();
const panel=nodes.get('change-evidence-recovery');assert(text(panel).includes('could not be bound'));
assert(!panel.contains(nodes.get('change-recovery-form')));assert.equal(commands().length,0);
''',
    "completed_or_rejected_change_never_asks_for_another_approval": r'''
Object.assign(item.recovery,{state:'READY_FOR_REVIEW',recovery_digest:'recovery:ready',allowed_actions:[]});
for(const [status,expected] of [['APPROVED','authorized executor'],['APPLIED','committed result'],['REJECTED','will not continue']]){
 item.status=status;await window.OrgRebaseChangeWorkbench.refresh();
 assert(nodes.get('change-recovery-next').textContent.includes(expected));
 assert(!nodes.get('change-recovery-next').textContent.includes('before approving'));
}
assert.equal(commands().length,0);
''',
    "history_identifies_requester_without_making_them_the_evidence_submitter": r'''
Object.assign(item.recovery,{state:'READY_FOR_REVIEW',recovery_digest:'recovery:ready',allowed_actions:[],
 requested_by:'finance-owner',submitted_by:'sales',history:[{round:1,resume_digest:'resume:one',requested_by:'finance-owner',reason:'Check evidence'}]});
await window.OrgRebaseChangeWorkbench.refresh();
assert(text(nodes.get('change-recovery-context')).includes('Evidence supplied · Returned by: finance-owner'));
assert(text(nodes.get('change-evidence-recovery')).includes('Evidence supplied by sales'));
document.documentElement.lang='zh-CN';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert(text(nodes.get('change-recovery-context')).includes('已提交补证 · 退回人：finance-owner'));
assert.equal(commands().length,0);
''',  # noqa: RUF001 - Match the Chinese UI punctuation exactly.
    "fresh_approval_binds_current_recovery_record": r'''
Object.assign(item.recovery,{state:'READY_FOR_REVIEW',recovery_digest:'recovery:ready',allowed_actions:[]});
item.allowed_actions=['APPROVE'];item.preview={preview_digest:'preview:ready',review_gate:{gate:{preview_digest:'preview:ready',not_before_epoch_ms:0}},
 bundle:{minimal_rebase_certificate:{effects:[]}}};await window.OrgRebaseChangeWorkbench.refresh();
const ack=nodes.get('change-review-ack');ack.checked=true;await ack.emit('change');
await nodes.get('change-approve-exact').emit('click');
const approval=commands().find(c=>c.path.includes('/approve/'));assert(approval);
assert.equal(approval.request.body.recovery_digest,'recovery:ready');assert.equal(approval.request.body.actor_id,'finance-owner');
assert.equal(approval.request.body.preview_digest,'preview:ready');assert(!commands().some(c=>c.path.includes('/apply/')));
''',
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_change_evidence_continuation(scenario):
    run_node(HARNESS + "\n(async()=>{await ready();\n" + SCENARIOS[scenario]
             + "\n})().catch(error=>{console.error(error);process.exitCode=1;});")
