from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


def test_attempt_summary_never_infers_approval_or_billing_and_never_retries():
    source = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS),calls=[];
const event={event_id:'event:failed',digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:product',base_version:'v1',base_digest:'sha256:base',proposal:{id:'D1',payload:{canonical_value:'New plan'},source_refs:['source:product']}};
const item={execution_run_id:'run:one',event,status:'RECEIVED',allowed_actions:['REJECT'],advisory_attempt:{state:'RESULT_UNKNOWN',usage_status:'UNKNOWN',public_error_code:'OPENAI_TIMEOUT',cost_reservation:{scope:'ONE_PREVIEW_ATTEMPT',currency:'USD',reserved_microusd:12500,limit_microusd:50000,calls:1},receipt_summaries:[{dispatch_state:'SENT_UNKNOWN',provider_request_id:null,input_tokens:null,output_tokens:null,private_body:'secret'}]}};
const session={mode:'local',principal:null};
window.OrgRebaseClient={session:()=>session,workspace:()=>'workspace:one',async json(path,request={}){
 calls.push({path,request});if(path.endsWith('/change-options'))return {execution_run_id:'run:one',fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');
(async()=>{
 await tick();await tick();let summary=text(nodes.get('change-advisory-attempt'));
 assert(summary.includes('USD 0.012500'));assert(summary.includes('USD 0.050000'));
 assert(summary.includes('Unknown; do not count as zero usage'));assert(summary.includes('SENT_UNKNOWN'));
 assert(summary.includes('not an actual bill'));assert(!summary.includes('secret'));
 assert.equal(button('Reject with reason').disabled,false);
 item.advisory_attempt.state='COMPLETE';item.advisory_attempt.usage_status='OBSERVED';item.advisory_attempt.cost_reservation=null;item.advisory_attempt.receipt_summaries=[];item.allowed_actions=[];
 await window.OrgRebaseChangeWorkbench.select(event.event_id);summary=text(nodes.get('change-advisory-attempt'));
 assert(summary.includes('This candidate was verified'));assert(summary.includes('does not mean the proposal was admitted, approved, or applied'));
 assert(!summary.includes('USD 0.012500'));assert.equal(nodes.get('change-detail-title').textContent,'Product plan · Awaiting preview');
 assert.equal(calls.filter(call=>call.request.method==='POST').length,0);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_domain_advice_is_plain_review_text_and_does_not_change_approval() -> None:
    source = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS),calls=[];
let previewFailure=null;
const event={event_id:'event:one',digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:product',base_version:'v1',base_digest:'sha256:base',proposal:{id:'claim:product.plan',payload:{canonical_value:'New plan'},source_refs:['source:product']}};
const item={execution_run_id:'run:one',event,status:'PREVIEWED',allowed_actions:['APPROVE'],preview:{preview_digest:'sha256:preview',review_gate:{gate:{preview_digest:'sha256:preview',not_before_epoch_ms:0}},bundle:{minimal_rebase_certificate:{effects:[{target_id:'work:quote',disposition:'REBUILD'}]},advisory:{handoffs:[{payload:{kind:'SemanticExplanation'}}]}}}};
const configuration={execution_run_id:'run:one',fields:[{slot_id:'product_plan',value_kind:'text',current:{version:'v1',digest:'sha256:base',value:'Old plan',state:'CURRENT'},owner_id:event.owner_id,allowed_operations:['UPDATE']}]};
window.businessChangeObjectLabel=id=>id==='claim:product.plan'?'Product plan':'Customer quote';
const session={mode:'local',principal:null};
window.OrgRebaseClient={session:()=>session,workspace:()=>'workspace:one',async json(path,request={}){
 calls.push({path,request});
 if(path.includes('/preview/')&&previewFailure)throw previewFailure;
 if(path==='/api/workspace/change-options')return structuredClone(configuration);
 if(path.startsWith('/api/workspace/changes?'))return {items:[structuredClone(item)],next_cursor:null};
 return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto:{randomUUID:()=>''}});
const visibleAdvice=()=>nodes.get('change-detail-body').children.find(node=>node.id==='change-domain-advice');
(async()=>{
 await tick();await tick();
 assert.equal(visibleAdvice(),undefined,'reference previews do not gain an empty advice section');
 const explanation='<img id="advice-pwned" src=x onerror="approve()">\nReview the revised product scope.';
 const generated=(domain_id,text)=>({payload:{model_advisory:{request:{schema_name:'private-schema'},receipt:{status:'VALID',value:{domain_id,object_ids:['claim:product.plan'],source_refs:['private-handoff-ref'],explanation:text}}}}});
 item.preview.bundle.advisory.handoffs.push(generated('product',explanation),generated('gtm','Update the customer-facing summary.'),generated('legal','   '));
 await window.OrgRebaseChangeWorkbench.select(event.event_id);
 const advice=visibleAdvice();assert.equal(advice.children.length,2);
 assert.equal(advice.children[0].children[0].textContent,'Product · Product plan');
 assert.equal(advice.children[0].children[1].textContent,explanation);
 assert.equal(advice.children[0].children[1].children.length,0,'model text is never interpreted as HTML');
 assert.equal(nodes.has('advice-pwned'),false);
 assert.equal(advice.children[1].children[0].textContent,'Commercial · Product plan');
 assert.equal(button('Approve exact proposal').disabled,true,'advice cannot acknowledge the impact review');
 assert.equal(nodes.get('change-review-ack').checked,false);
 const text=node=>[node.textContent||'',...node.children.map(text)].join('\n');
 assert.equal(text(advice).includes('private-schema'),false);
 assert.equal(text(advice).includes('private-handoff-ref'),false);
 assert(nodes.get('change-detail-body').children.some(node=>node.textContent?.includes('Approval remains bound')));
 nodes.get('change-review-ack').checked=true;await nodes.get('change-review-ack').emit('change');
 assert.equal(button('Approve exact proposal').disabled,false,'the existing impact acknowledgement still controls approval');
 item.preview.bundle.advisory.handoffs=[];
 await window.OrgRebaseChangeWorkbench.select(event.event_id);
 assert.equal(visibleAdvice(),undefined);
 assert.equal(nodes.get('change-review-ack').checked,true,'removing explanatory text does not rewrite the exact preview acknowledgement');
 assert.equal(calls.filter(call=>call.request.method==='POST').length,0,'rendering suggestions dispatches no commands');
 item.allowed_actions=['PREVIEW'];await window.OrgRebaseChangeWorkbench.select(event.event_id);
 for(const [code,expected] of [
  ['WORKSPACE_ADVISORY_IN_PROGRESS','Refresh later'],
  ['WORKSPACE_ADVISORY_RESULT_UNKNOWN','Ask the owner to reject'],
  ['WORKSPACE_ADVISORY_ATTEMPT_FAILED:OPENAI_TIMEOUT','Ask the owner to reject'],
  ['WORKSPACE_ADVISORY_INPUT_CHANGED_REQUIRE_NEW_EVENT','Ask the owner to reject'],
  ['OPENAI_CREDENTIALS_MISSING','Ask an administrator'],
 ]){
  previewFailure=Object.assign(new Error(code),{code});
  const before=calls.filter(call=>call.path.includes('/preview/')).length;
  await button('Check change impact').emit('click');await tick();
  const notice=nodes.get('change-notice');assert.equal(notice.hidden,false);assert(notice.textContent.includes(expected));
  assert.equal(calls.filter(call=>call.path.includes('/preview/')).length,before+1,'recovery guidance never automatically dispatches another attempt');
 }
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_change_editor_retries_exact_proposal_and_reject_revision_needs_new_review() -> None:
    source = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict');const vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);
const calls=[];const items=[];let uuid=0;let loseResponse=true;
const configuration={execution_run_id:'run:one',fields:[{slot_id:'product_plan',label:'Product plan',value_kind:'text',current:{version:'v1',digest:'sha256:base-one',value:'Original',state:'CURRENT'},owner_id:'owner:product',source_refs:['source:old'],allowed_operations:['UPDATE']}]};
function preview(item){item.status='PREVIEWED';item.allowed_actions=['PREVIEW','APPROVE','REJECT'];item.preview={preview_digest:'sha256:preview-'+item.event.event_id,review_gate:{gate:{preview_digest:'sha256:preview-'+item.event.event_id,not_before_epoch_ms:0}},bundle:{minimal_rebase_certificate:{effects:[{target_id:'work:quote',disposition:'REBUILD'},{target_id:'work:unaffected',disposition:'PRESERVE_WITHIN_BOUNDARY'}]}}};}
const session={mode:'local',principal:null};
window.OrgRebaseClient={session:()=>session,workspace:()=> 'workspace:one',async json(path,request={}){
  calls.push({path,request:structuredClone(request)});
  if(path==='/api/workspace/change-options')return structuredClone(configuration);
  if(path.startsWith('/api/workspace/changes?'))return {items:structuredClone(items),next_cursor:null,total:items.length};
  if(path==='/api/workspace/change-proposals'){
    const body=request.body;let item=items.find(item=>item.event.event_id===body.event_id);
    if(!item){item={execution_run_id:'run:one',event:{event_id:body.event_id,slot_id:body.slot_id,owner_id:'owner:product',base_version:body.base_version,base_digest:body.base_digest,proposal:{payload:{canonical_value:body.value},source_refs:[body.source_ref]}},status:'RECEIVED',allowed_actions:['PREVIEW','REJECT'],reason:body.reason,revises_event_id:body.revises_event_id};items.push(item);}
    if(loseResponse){loseResponse=false;throw new Error('NETWORK_LOST');}
    return {event_id:body.event_id};
  }
  const id=decodeURIComponent(path.split('/').at(-1));
  if(path.includes('/preview/')){const item=items.find(item=>item.event.event_id===id);preview(item);return {state:{}};}
  if(path.endsWith('/review-observation'))return {canonical_writes:0};
  if(path.endsWith('/reject')){const item=items.find(item=>path.includes(encodeURIComponent(item.event.event_id)));item.status='REJECTED';item.allowed_actions=['REVISE'];item.rejection={reason:request.body.reason};return {};}
  if(path.includes('/approve/')){const item=items.find(item=>item.event.event_id===id);assert.equal(request.body.preview_digest,item.preview.preview_digest);item.status='APPROVED';item.allowed_actions=['APPLY'];item.approval={approval_digest:'sha256:approval-'+id};return structuredClone(item.approval);}
  if(path.includes('/apply/')){const item=items.find(item=>item.event.event_id===id);assert.equal(request.body.approval_digest,item.approval.approval_digest);item.status='APPLIED';item.allowed_actions=[];return {};}
  const item=items.find(item=>item.event.event_id===id);if(!item)throw new Error('UNKNOWN_ROUTE:'+path);return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto:{randomUUID:()=>String(++uuid)}});
(async()=>{
  await tick();await tick();
  nodes.get('change-value').value='Revised plan';await nodes.get('change-value').emit('input');
  nodes.get('change-source').value='source:new-evidence';await nodes.get('change-source').emit('input');
  nodes.get('change-proposal-reason').value='Renew the commercial scope';await nodes.get('change-proposal-reason').emit('input');
  configuration.fields[0].current.version='v2';configuration.fields[0].current.digest='sha256:base-two';
  await window.OrgRebaseChangeWorkbench.refresh();
  assert.equal(nodes.get('change-value').value,'Revised plan','new base must not discard draft');
  assert.equal(nodes.get('change-submit').disabled,true,'stale draft cannot submit against silently substituted base');
  await nodes.get('change-proposal-form').emit('submit');assert.equal(items.length,0);
  await nodes.get('change-cancel').emit('click');
  assert.equal(nodes.get('change-value').value,'Revised plan');
  await nodes.get('change-proposal-form').emit('submit');
  assert.equal(items.length,1);
  assert.equal(items[0].event.base_version,'v2');
  assert.equal(nodes.get('change-value').value,'Revised plan','failed response preserves draft');
  await nodes.get('change-proposal-form').emit('submit');await tick();
  const submitted=calls.filter(call=>call.path==='/api/workspace/change-proposals');
  assert.deepEqual(submitted[0].request.body,submitted[1].request.body,'uncertain response retries same immutable event');
  assert.equal(items.length,1);
  configuration.fields[0].current.version='v3';configuration.fields[0].current.digest='sha256:base-three';
  await window.OrgRebaseChangeWorkbench.refresh();
  assert.equal(nodes.get('change-submit').disabled,false,'an untouched empty draft follows the current base without a conflict warning');
  await button('Check change impact').emit('click');await tick();
  assert.equal(button('Approve exact proposal').disabled,true);
  assert(nodes.get('change-detail-body').children.some(el=>el.tagName==='UL'&&el.children.length===2),'complete effect set displayed');
  const certificate=items[0].preview.bundle.minimal_rebase_certificate;
  items[0].preview.bundle.minimal_rebase_certificate=null;
  await window.OrgRebaseChangeWorkbench.select(items[0].event.event_id);
  assert.equal(nodes.get('change-review-ack').disabled,true,'missing complete certificate cannot be acknowledged');
  items[0].preview.bundle.minimal_rebase_certificate=certificate;
  items[0].preview.review_gate.gate.preview_digest='sha256:foreign-preview';
  await window.OrgRebaseChangeWorkbench.select(items[0].event.event_id);
  assert.equal(button('Review status unverified').disabled,true,'gate for another preview cannot enable approval');
  items[0].preview.review_gate.gate.preview_digest=items[0].preview.preview_digest;
  await window.OrgRebaseChangeWorkbench.select(items[0].event.event_id);
  nodes.get('change-detail-reason').value='Source evidence needs correction';
  const rejectForm=nodes.get('change-detail-reason').parent.parent;
  await rejectForm.emit('submit');await tick();
  assert.equal(items[0].status,'REJECTED');
  await button('Revise as new proposal').emit('click');
  assert.equal(nodes.get('change-value').value,'Revised plan');
  assert.equal(nodes.get('change-source').value,'source:new-evidence');
  assert.equal(nodes.get('change-proposal-reason').value,'Renew the commercial scope','revision preserves the business reason separately from the rejection reason');
  assert.equal(nodes.get('change-editor').open,true);
  nodes.get('change-value').value='Corrected plan';await nodes.get('change-value').emit('input');
  await nodes.get('change-proposal-form').emit('submit');await tick();
  assert.equal(items.length,2);assert.equal(items[1].revises_event_id,items[0].event.event_id);
  assert.equal(items[1].event.base_version,'v3','revision uses the reviewed current base');
  assert.notEqual(items[1].event.event_id,items[0].event.event_id);
  assert.equal(items[0].status,'REJECTED','old decision is not rewritten');
  await button('Check change impact').emit('click');await tick();
  assert.equal(nodes.get('change-review-ack').checked,false,'revision never reuses prior acknowledgement');
  nodes.get('change-review-ack').checked=true;await nodes.get('change-review-ack').emit('change');
  assert.equal(document.activeElement,nodes.get('change-review-ack'),'keyboard acknowledgement retains focus after rendering');
  const optionReadsBeforeApproval = calls.filter(call => call.path === '/api/workspace/change-options').length;
  await button('Approve exact proposal').emit('click');await tick();
  assert.equal(calls.filter(call => call.path === '/api/workspace/change-options').length, optionReadsBeforeApproval + 1, 'a successful command must not refresh its own workbench twice');
  assert.equal(items[1].status,'APPLIED');
  assert.equal(calls.filter(call=>call.path.includes('/approve/')).length,1);
  assert.equal(calls.filter(call=>call.path.includes('/apply/')).length,1,'one explicit approval continues through the independently authorized apply endpoint');
  const observations=calls.filter(call=>call.path.endsWith('/review-observation'));
  assert(observations.length>0);
  for(const call of observations){assert(call.request.body.observation_id);assert(call.request.body.active_ms>=0);assert.equal(call.request.body.outcome,'SUCCESS');}
  const notifications = [];
  window.addEventListener('orgrebase:changeselection', event => notifications.push(event));
  const posts = calls.filter(call => call.request?.method === 'POST').length;
  await window.OrgRebaseChangeWorkbench.select(items[0].event.event_id);
  assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail().event.event_id, items[0].event.event_id);
  assert(notifications.length > 0, 'read-only selection changes notify the receipt overview');
  await window.OrgRebaseChangeWorkbench.refresh();
  assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail().event.event_id, items[0].event.event_id);
  assert.equal(calls.filter(call => call.request?.method === 'POST').length, posts, 'selection and refresh do not mutate authority');
  window.dispatchEvent(new CustomEvent('orgrebase:sessionended', {detail:{reason:'expired'}}));
  assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail(), null, 'ending a session clears the selected receipt source');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace("HARNESS", harness).replace("SOURCE", source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_current_completion_projection_binds_rejected_and_applied_events_to_v2_archive() -> None:
    app = (ROOT / "demo/console/app.js").read_text()
    projection = app[app.index("function isObservedCount"):app.index("function currentRunValueCard")]
    archive = app[app.index("const verifiedApprovalAuthorities"):app.index("function completedRunObservabilityProjection")]
    run_node(projection + archive + r'''
const assert=require('node:assert/strict');
const digest=char=>'sha256:'+char.repeat(64),runId='run:one';
function isSha256Digest(value){return typeof value==='string'&&/^sha256:[0-9a-f]{64}$/.test(value);}
function activeCompetition(){return null;}function stateExecution(state){return state.execution;}
function receiptForChange(records){return records.receipt;}
function changeProjection(_id,records){return records;}
const applied={receiptComplete:true,receipt:{digest:digest('c'),status:'COMPLETED',workflow_run_id:runId,metrics:{work_items_rebased:1,bounded_unaffected:2,unknown:1,skills_requalified:0,false_invalidations:0,unauthorized_disclosures:0}},preview:{preview_digest:digest('a')},approval:{approval_digest:digest('b')},outcome:{outcome:{workspace_rebase_receipt:{digest:digest('d')}}},workspaceReceipt:{status:'COMPLETED',digest:digest('d')},outcomeQuote:{version:'v2',digest:digest('f')},metrics:{rebuilt:1,preserved:2,unknown:1,falseInvalidations:0,unauthorized:0}};
const dispositions=[{event_id:'rejected:1',event_digest:digest('1'),status:'REJECTED',slot_id:'product_plan',owner_id:'owner:product'},{event_id:'revised:2',event_digest:digest('2'),status:'APPLIED',slot_id:'product_plan',owner_id:'owner:product'}];
const terminal={schema_version:'orgrebase.workspace-state.v2',stage:'CURRENT',business_complete:true,execution:{run_id:runId},quote:{id:'work:quote',version:'v2',digest:digest('f')},event_scopes:{quote_business:{status:'PASS',events:12}},change_events:structuredClone(dispositions),change_history:{total:2},changes:{'rejected:1':{preview:{preview_digest:digest('e')}},'revised:2':applied}};
let currentRunArchive={schema_version:'orgrebase.workspace-current-run-archive-view.v2',status:'ARCHIVED',business_complete:true,failures:[],run_id:runId,claim_boundary:'CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING',record:{archive_class:'CURRENT_BUSINESS_RUN_COMPLETION',same_run_as_current_task:true,terminal_status:'COMPLETED',run_id:runId,human_approval_count:1,quote:{ref:'work:quote@v2',digest:digest('f')},quote_event_count:12,canonical_authority:'ORGREBASE_CONTROL_PLANE',evidence_class:'VERIFIED_SAME_RUN_CANONICAL_STATE',change_dispositions:dispositions,selective_rebase_receipts:[{kind:'revised:2',owner_id:'owner:product',preview_digest:digest('a'),approval_digest:digest('b'),rebase_receipt_digest:digest('c'),workspace_receipt_digest:digest('d'),successor_quote_version:'v2'}]}};
const clean=structuredClone(currentRunArchive);
assert.equal(currentRunValueProjection(terminal).status,'PASS');
assert.equal(currentRunValueProjection(terminal).receiptCount,1,'a rejected event never fabricates an approval or apply');
for(const mutate of [state=>{state.change_events[0].event_digest=digest('9');},state=>{delete state.change_events[1].event_digest;},state=>{state.change_events[0].status='APPLIED';},state=>{state.change_events[1].owner_id='wrong';},state=>{state.changes['revised:2'].preview.preview_digest=digest('e');},state=>{state.quote.id='work:other';}]){
 const bad=structuredClone(terminal);mutate(bad);assert.equal(currentRunValueProjection(bad).status,'INVALID');
}
currentRunArchive.record.change_dispositions=[dispositions[1],dispositions[1]];
assert.equal(currentRunValueProjection(terminal).status,'INVALID');
currentRunArchive=structuredClone(clean);currentRunArchive.record.evidence_class='VERIFIED_SAME_RUN_CONTROLLED_LOCAL';
assert.equal(currentRunArchiveProof(currentRunArchive,runId),false,'v2 cannot borrow the legacy evidence class');
currentRunArchive=structuredClone(clean);currentRunArchive.schema_version='orgrebase.workspace-current-run-archive-view.v99';
assert.equal(currentRunArchiveProof(currentRunArchive,runId),false);
currentRunArchive=null;assert.equal(currentRunValueProjection(terminal).status,'WAITING_FOR_VERIFIED_ARCHIVE');

(async()=>{
 const identity={issuer:'https://id.example',subject:'reviewer',actor_id:'delegate:reviewer'};
 const grant={event_id:'revised:2',event_digest:digest('2'),tenant_id:'tenant:one',workspace_id:'workspace:one',execution_run_id:runId,owner:{issuer:identity.issuer,subject:'original',actor_id:'owner:product'},delegate:identity,issued_at:'2026-09-09T10:00:00Z',expires_at:'2026-09-09T10:15:00Z',actions:['APPROVE','REJECT'],reason:'复核',command_digest:digest('9')};
 const hash=async value=>'sha256:'+Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalAuthorityDocument(value)))).toString('hex');
 const grantDigest=await hash(grant),provenance={approval_digest:digest('b'),event_digest:digest('2'),identity,grant_digest:grantDigest};
 const authority={schema_version:'orgrebase.workspace-approval-authority.v1',evidence_class:'VERIFIED_EVENT_SCOPED_APPROVAL_AUTHORITY',provenance,provenance_digest:await hash(provenance),grant,grant_digest:grantDigest};
 currentRunArchive=structuredClone(clean);
 Object.assign(currentRunArchive.record.selective_rebase_receipts[0],{approval_actor_id:identity.actor_id,approval_authority:authority});
 const authorized=structuredClone(terminal);authorized.workspace_id='workspace:one';authorized.enterprise_seed_profile={organization_id:'tenant:one'};
 Object.assign(authorized.changes['revised:2'].approval,{authority:structuredClone(authority),approval:{actor_id:identity.actor_id,approved_at:'2026-09-09T10:05:00Z'}});
 await verifyArchiveApprovalAuthorities(currentRunArchive);
 assert.equal(currentRunValueProjection(authorized).status,'PASS');
 for(const mutate of [state=>{state.workspace_id='other';},state=>{state.enterprise_seed_profile.organization_id='other';},state=>{state.changes['revised:2'].approval.approval.actor_id='other';},state=>{state.changes['revised:2'].approval.approval.approved_at='2026-09-09T09:59:59Z';},state=>{state.changes['revised:2'].approval.approval.approved_at=grant.expires_at;},state=>{state.changes['revised:2'].approval.authority.provenance.identity.subject='other';}]){
  const bad=structuredClone(authorized);mutate(bad);assert.equal(currentRunValueProjection(bad).status,'INVALID','archive authority cannot be borrowed across state, subject, scope or validity interval');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});

''')


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_delegation_and_escalation_bind_one_event_without_creating_approval() -> None:
    source = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);const calls=[];
const event={event_id:'event:one',digest:'sha256:event-one',slot_id:'product_plan',owner_id:'owner:product',base_version:'v1',base_digest:'sha256:base',proposal:{payload:{canonical_value:'New plan'},source_refs:['source:one']}};
const item={execution_run_id:'run:one',event,status:'PREVIEWED',allowed_actions:['DELEGATE','ESCALATE'],authority:{owner_id:event.owner_id,active_owner_id:event.owner_id,eligible_delegates:[{subject:'member-42',actor_id:'reviewer:backup',label:'Backup reviewer'}],delegation:null,delegation_active:false,escalation:null},preview:null,approval:null};
const options={execution_run_id:'run:one',fields:[{slot_id:'product_plan',value_kind:'text',current:{version:'v1',digest:'sha256:base',value:'Old plan',state:'CURRENT'},owner_id:event.owner_id,allowed_operations:['UPDATE']}]};
const session={mode:'oidc',principal:{actor_id:event.owner_id}};
window.OrgRebaseClient={session:()=>session,async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path==='/api/workspace/change-options')return structuredClone(options);
 if(path.startsWith('/api/workspace/changes?'))return {items:[structuredClone(item)],next_cursor:null};
 if(path.endsWith('/delegate')){assert.equal(request.body.event_digest,event.digest);assert.equal(request.body.subject,'member-42');assert.equal(request.body.actor_id,'reviewer:backup');assert.equal(request.body.valid_seconds,900);item.authority.delegation={delegate:{actor_id:'reviewer:backup'},expires_at:'later'};item.authority.delegation_active=true;item.active_owner_id='reviewer:backup';item.allowed_actions=['REVOKE_DELEGATION','ESCALATE'];}
 if(path.endsWith('/escalate')){assert.equal(request.body.event_digest,event.digest);item.authority.escalation={reason:request.body.reason};item.allowed_actions=['REVOKE_DELEGATION'];}
 if(path.endsWith('/revoke-delegation')){assert.equal(request.body.event_digest,event.digest);item.authority.delegation_active=false;item.active_owner_id=event.owner_id;item.authority.blocked_reason='DELEGATION_REVOKED';item.allowed_actions=[];}
 return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto:{randomUUID:()=>''}});
(async()=>{
 await tick();await tick();
 assert.equal(nodes.get('change-delegate-member').children[1].textContent,'Backup reviewer');
 nodes.get('change-delegate-member').value=JSON.stringify(['member-42','reviewer:backup']);nodes.get('change-delegate-reason').value='Owner unavailable during approved review window';
 await button('Delegate this proposal').parent.emit('submit');await tick();
 assert.equal(item.authority.delegation_active,true);assert.equal(item.approval,null);assert.equal(item.event.owner_id,'owner:product');
 nodes.get('change-escalate-reason').value='Coordinate with backup reviewer';await button('Request escalation').parent.emit('submit');await tick();
 assert.equal(item.approval,null);assert.equal(item.allowed_actions.includes('APPROVE'),false,'escalation does not grant approval authority');
 nodes.get('change-revoke_delegation-reason').value='Original owner returned';await button('Revoke delegation').parent.emit('submit');await tick();
 assert.equal(item.authority.delegation_active,false);assert.equal(item.active_owner_id,event.owner_id);
 assert.equal(calls.filter(call=>call.path.includes('/approve/')||call.path.includes('/apply/')).length,0,'coordination never becomes a business decision');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_browser_verifies_authority_hashes_and_exact_delegate_provenance() -> None:
    from orgrebase.digest import sha256_digest

    owner = {"issuer": "https://identity.example", "subject": "owner-subject", "actor_id": "owner:one"}
    delegate = {"issuer": owner["issuer"], "subject": "delegate-subject", "actor_id": "reviewer:two"}
    grant = {"event_id": "event:one", "event_digest": "sha256:" + "e" * 64, "tenant_id": "tenant:one",
             "workspace_id": "quote-one", "execution_run_id": "run:one", "owner": owner, "delegate": delegate,
             "issued_at": "2026-09-09T00:00:00Z", "expires_at": "2026-09-09T00:15:00Z",
             "actions": ["APPROVE", "REJECT"], "reason": "负责人休假并委派本次审阅", "command_digest": "sha256:" + "c" * 64}
    provenance = {"approval_digest": "sha256:" + "a" * 64, "event_digest": grant["event_digest"],
                  "identity": delegate, "grant_digest": sha256_digest(grant)}
    authority = {"schema_version": "orgrebase.workspace-approval-authority.v1", "provenance": provenance,
                 "provenance_digest": sha256_digest(provenance), "grant": grant, "grant_digest": sha256_digest(grant),
                 "evidence_class": "VERIFIED_EVENT_SCOPED_APPROVAL_AUTHORITY"}
    app = (ROOT / "demo/console/app.js").read_text()
    helper = app[app.index("const verifiedApprovalAuthorities"):app.index("function currentRunArchiveProof")]
    run_node(helper + r'''
const assert=require('node:assert/strict');
function isSha256Digest(value){return typeof value==='string'&&/^sha256:[0-9a-f]{64}$/.test(value);}
const authority=AUTHORITY;
const receipt={kind:'event:one',owner_id:'owner:one',approval_actor_id:'reviewer:two',approval_digest:authority.provenance.approval_digest,approval_authority:authority};
const view=receipt=>({schema_version:'orgrebase.workspace-current-run-archive-view.v2',record:{selective_rebase_receipts:[receipt]}});
async function hash(value){return 'sha256:'+Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalAuthorityDocument(value)))).toString('hex');}
async function reseal(value){value.grant_digest=await hash(value.grant);value.provenance.grant_digest=value.grant_digest;value.provenance_digest=await hash(value.provenance);}
(async()=>{
 assert.equal(archiveApprovalAuthorityBound(receipt,'run:one'),false,'unverified document cannot claim authority');
 await verifyArchiveApprovalAuthorities(view(receipt));
 assert.equal(archiveApprovalAuthorityBound(receipt,'run:one',authority.provenance.event_digest),true,'WebCrypto matches Python canonical hashes including Chinese text');
 assert.equal(archiveApprovalAuthorityBound(receipt,'run:other'),false);
 assert.equal(archiveApprovalAuthorityBound(receipt,'run:one','sha256:'+'0'.repeat(64)),false);
 for(const mutate of [value=>value.grant.owner.actor_id='other-owner',value=>value.grant.event_id='event:other',value=>value.grant.execution_run_id='run:other',value=>value.provenance.identity.subject='forged',value=>value.provenance.approval_digest='sha256:'+'0'.repeat(64),value=>value.grant.actions=['APPLY'],value=>value.grant.delegate.issuer='https://foreign.example']){
  const copy=structuredClone(receipt);mutate(copy.approval_authority);await reseal(copy.approval_authority);await verifyArchiveApprovalAuthorities(view(copy));
  assert.equal(archiveApprovalAuthorityBound(copy,'run:one'),false,'self-consistent hashes cannot replace exact authority relations');
 }
 const corrupted=structuredClone(receipt);corrupted.approval_authority.grant.reason='Rewritten';
 await assert.rejects(verifyArchiveApprovalAuthorities(view(corrupted)),/DIGEST_INVALID/);
 const unknown=structuredClone(receipt);unknown.approval_authority.schema_version='orgrebase.workspace-approval-authority.v99';
 await assert.rejects(verifyArchiveApprovalAuthorities(view(unknown)),/DIGEST_INVALID/);
 authority.grant.reason='Mutated after verification';
 assert.equal(archiveApprovalAuthorityBound(receipt,'run:one'),false,'cached verification cannot outlive mutated bytes');
 assert.equal(archiveApprovalAuthorityBound({...receipt,approval_authority:undefined},'run:one'),false,'actual delegate cannot omit provenance');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('AUTHORITY', json.dumps(authority, ensure_ascii=False)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_completion_cache_rebinds_same_run_quote_events_and_workspace() -> None:
    app = (ROOT / "demo/console/app.js").read_text()
    keys = app[app.index("function terminalRunId"):app.index("function clearCurrentRunArchive")]
    refresh = app[app.index("function refreshCompletedRunObservabilityProjection"):app.index("function renderWorkspaceUnavailable")]
    run_node(r'''
const assert=require('node:assert/strict');
let currentRunArchive=null,currentRunArchiveRequestedFor=null,currentRunObservability=null,currentRunObservabilityRequestedFor=null;
let workspaceSessionRevision=0;
let currentState={workspace_id:'quote-a',business_complete:true,execution:{run_id:'same-run'},quote:{id:'quote',version:'v2',digest:'digest-two'},change_history:{total:1},change_events:[{event_id:'one',event_digest:'event-one',status:'APPLIED'}],changes:{}};
const calls=[],archiveRenders=[];
function byId(){return null;}
function api(path){return new Promise((resolve,reject)=>calls.push({path,resolve,reject}));}
function renderCurrentRunArchive(view){archiveRenders.push(view);}
function renderOperations(){} function renderCurrentRunValue(){} function renderAcceptanceStory(){} function renderCurrentTaskBadge(){}
function renderCommand(){}
const window={dispatchEvent(){}};
class CustomEvent { constructor(type,options){this.type=type;this.detail=options.detail;} }
function currentRunArchiveProof(view,runId){return view?.run_id===runId&&view.status==='ARCHIVED';}
async function verifyArchiveApprovalAuthorities(){}
async function verifyCompletedRunObservability(){}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
''' + keys + refresh + r'''
(async()=>{
 refreshCurrentRunArchive();refreshCurrentRunArchive();
 assert.equal(calls.length,1,'a terminal snapshot has one in-flight archive request');
 const firstKey=currentRunCompletionKey(currentState);
 currentState={...currentState,quote:{...currentState.quote,version:'v3',digest:'digest-three'}};
 refreshCurrentRunArchive();assert.equal(calls.length,2,'same run with new canonical quote needs a new proof');
 calls[0].resolve({run_id:'same-run',status:'ARCHIVED',marker:'obsolete'});await tick();
 assert.equal(currentRunArchive,null,'a late old quote response cannot overwrite current proof');
 calls[1].resolve({run_id:'same-run',status:'ARCHIVED',marker:'current'});await tick();
 assert.equal(currentRunArchive.marker,'current');assert.equal(calls.length,3);
 assert.equal(calls[2].path,'/api/workspace/run-observability');
 refreshCurrentRunArchive();assert.equal(calls.length,3,'repeat render does not repeat full audit');
 currentState={...currentState,change_history:{total:2},change_events:[...currentState.change_events,{event_id:'two',event_digest:'event-two',status:'REJECTED'}]};
 refreshCurrentRunArchive();assert.equal(calls.length,4,'rejection changes completion without changing canonical quote');
 calls[2].resolve({marker:'stale-observability'});await tick();assert.equal(currentRunObservability,null);
 calls[3].resolve({run_id:'same-run',status:'ARCHIVED',marker:'after-rejection'});await tick();
 assert.equal(calls.length,5);calls[4].resolve({marker:'after-rejection'});await tick();
 assert.equal(currentRunObservability.marker,'after-rejection');
 const stableKey=currentRunCompletionKey(currentState);
 currentState={...currentState,change_events:[...currentState.change_events].reverse()};
 assert.equal(currentRunCompletionKey(currentState),stableKey,'ordering alone does not invalidate the same exact event set');
 refreshCurrentRunArchive();assert.equal(calls.length,5);
 currentState={...currentState,workspace_id:'quote-b'};refreshCurrentRunArchive();
 assert.equal(calls.length,6,'matching run and quote in another workspace cannot borrow proof');
 assert.notEqual(currentRunCompletionKey(currentState),firstKey);
 currentState={...currentState,business_complete:false};refreshCurrentRunArchive();
 calls[5].resolve({run_id:'same-run',status:'ARCHIVED',marker:'pending-overwrite'});await tick();
 assert.equal(currentRunArchive,null);assert.equal(currentRunArchiveRequestedFor,null);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_change_detail_keeps_local_review_inputs_without_reusing_authority_or_identity() -> None:
    source = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);
const elementPrototype=Object.getPrototypeOf(document.createElement('input'));
elementPrototype.selectionStart=0;elementPrototype.selectionEnd=0;
elementPrototype.setSelectionRange=function(start,end){this.selectionStart=start;this.selectionEnd=end;};
const first={subject:'member-first',actor_id:'reviewer:first',label:'First reviewer'};
const backup={subject:'member-backup',actor_id:'reviewer:backup',label:'Backup reviewer'};
const key=member=>JSON.stringify([member.subject,member.actor_id]);
const configuration={execution_run_id:'run:one',fields:[{slot_id:'product_plan',value_kind:'text',current:{version:'v1',digest:'sha256:base',value:'Original',state:'CURRENT'},owner_id:'owner:product',allowed_operations:['UPDATE']}]};
function record(id){const previewDigest='sha256:preview-'+id;return {execution_run_id:'run:one',event:{event_id:id,digest:'sha256:event-'+id,slot_id:'product_plan',owner_id:'owner:product',base_version:'v1',base_digest:'sha256:base',proposal:{payload:{canonical_value:'New plan'},source_refs:['source:one']}},status:'PREVIEWED',allowed_actions:['APPROVE','REJECT','DELEGATE','ESCALATE'],authority:{eligible_delegates:[first,backup]},preview:{preview_digest:previewDigest,review_gate:{gate:{preview_digest:previewDigest,not_before_epoch_ms:0}},bundle:{minimal_rebase_certificate:{effects:[]}}}};}
const items=[record('event:one'),record('event:two')],calls=[];
window.OrgRebaseClient={session:()=>({mode:'oidc',principal:{actor_id:'owner:product'}}),async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path==='/api/workspace/change-options')return structuredClone(configuration);
 if(path.startsWith('/api/workspace/changes?'))return {items:structuredClone(items),next_cursor:null};
 if(path.endsWith('/delegate'))return {};
 const id=decodeURIComponent(path.split('/').at(-1));return structuredClone(items.find(item=>item.event.event_id===id));
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto:{randomUUID:()=>''}});
(async()=>{
 await tick();await tick();
 nodes.get('change-detail-reason').value='Source evidence needs correction';
 nodes.get('change-delegate-member').value=key(backup);
 nodes.get('change-delegate-duration').value='12';
 nodes.get('change-delegate-reason').value='Owner unavailable';
 nodes.get('change-escalate-reason').value='Coordinate with the original owner';
 nodes.get('change-audit').open=true;nodes.get('change-authority').open=true;
 const reason=nodes.get('change-detail-reason');reason.focus();reason.setSelectionRange(7,15);
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.notEqual(nodes.get('change-detail-reason'),reason,'the test covers replacement of actual controls');
 assert.equal(nodes.get('change-detail-reason').value,'Source evidence needs correction');
 assert.equal(document.activeElement,nodes.get('change-detail-reason'));
 assert.equal(document.activeElement.selectionStart,7);assert.equal(document.activeElement.selectionEnd,15);
 assert.equal(nodes.get('change-delegate-duration').value,'12');
 assert.equal(nodes.get('change-delegate-reason').value,'Owner unavailable');
 assert.equal(nodes.get('change-escalate-reason').value,'Coordinate with the original owner');
 assert.equal(nodes.get('change-audit').open,true);assert.equal(nodes.get('change-authority').open,true);
 nodes.get('change-review-ack').checked=true;await nodes.get('change-review-ack').emit('change');
 assert.equal(nodes.get('change-detail-reason').value,'Source evidence needs correction','acknowledging effects must not erase a drafted rejection');
 assert.equal(document.activeElement,nodes.get('change-review-ack'));
 nodes.get('change-detail-reason').focus();document.documentElement.lang='zh-CN';
 window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
 assert.equal(nodes.get('change-detail-reason').value,'Source evidence needs correction');
 assert.equal(document.activeElement,nodes.get('change-detail-reason'));
 document.documentElement.lang='en';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
 items[0].preview.preview_digest='sha256:changed-preview';items[0].preview.review_gate.gate.preview_digest='sha256:changed-preview';
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(nodes.get('change-review-ack').checked,false,'ordinary form recovery never re-acknowledges a new preview');
 assert.equal(button('Approve exact proposal').disabled,true);
 assert.equal(nodes.get('change-detail-reason').value,'Source evidence needs correction');
 items[0].authority.eligible_delegates=[backup,first];
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(nodes.get('change-delegate-member').value,key(backup),'member order cannot change the selected identity');
 await button('Delegate this proposal').parent.emit('submit');await tick();
 const delegated=calls.find(call=>call.path.endsWith('/delegate')).request.body;
 assert.equal(delegated.subject,backup.subject);assert.equal(delegated.actor_id,backup.actor_id);assert.equal(delegated.valid_seconds,720);
 items[0].authority.eligible_delegates=[{...backup,subject:'replacement-subject'},first];
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(nodes.get('change-delegate-member').value,'','the same actor with a different subject cannot inherit a selection');
 const posts=calls.filter(call=>call.request.method==='POST').length;
 await button('Delegate this proposal').parent.emit('submit');
 assert.equal(calls.filter(call=>call.request.method==='POST').length,posts,'an ineligible selection cannot submit');
 const outside=document.createElement('button');outside.focus();await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(document.activeElement,outside,'background refresh does not take focus from another task');
 await window.OrgRebaseChangeWorkbench.select('event:two');
 assert.equal(nodes.get('change-detail-reason').value,'');assert.equal(nodes.get('change-delegate-reason').value,'');
 assert.equal(nodes.get('change-delegate-duration').value,'15');assert.notEqual(nodes.get('change-audit').open,true);
 await window.OrgRebaseChangeWorkbench.select('event:one');
 assert.equal(nodes.get('change-detail-reason').value,'','a previous selection has no retained hidden form cache');
 nodes.get('change-detail-reason').value='Same event ID in another run must not inherit this';
 configuration.execution_run_id='run:two';for(const item of items)item.execution_run_id='run:two';
 await window.OrgRebaseChangeWorkbench.refresh();assert.equal(nodes.get('change-detail-reason').value,'');
 nodes.get('change-detail-reason').value='Private unsent rejection';
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));
 assert.equal(nodes.get('change-detail-body').children.length,0);
 await window.OrgRebaseChangeWorkbench.refresh();assert.equal(nodes.get('change-detail-reason').value,'');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))


def test_history_rows_distinguish_current_review_from_historical_decisions():
    source = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
const statuses=['PREVIEWED','PREVIEWED','APPROVED','APPLIED','REJECTED','RECOVERY_REQUIRED','GROUP_REVIEW','EXPIRED','STALE','GROUP_APPLIED','EXPIRED'];
const items=statuses.map((status,index)=>({status,event:{event_id:'change-'+index,digest:'sha256:event',slot_id:'product_plan',owner_id:'owner:original',base_version:'v1',base_digest:'sha256:base',proposal:{id:'claim:product.plan',payload:{canonical_value:'Revised plan'},source_refs:['source:product']}},responsibility:{owner_id:'owner:original',delegate_id:'actor:delegate',decision_actor_id:'actor:historical',blocked_reason:index===1?'DELEGATION_REVOKED':index===7?'CHANGE_OWNER_REPLAN_REQUIRED':index===10?'DELEGATION_EXPIRED':null}}));
window.OrgRebaseClient={session:()=>({mode:'local',principal:null}),workspace:()=>'workspace:one',async json(path){
 if(path.endsWith('/change-options'))return {execution_run_id:'run:one',fields:[]};
 if(path.includes('/changes?'))return {items,next_cursor:null};
 return {...items[0],execution_run_id:'run:one',allowed_actions:[]};
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');
(async()=>{
 await tick();await tick();const rows=nodes.get('change-event-list').children.map(text);
 assert.equal(rows.length,11);assert(rows.every(row=>row.includes('Original owner: owner:original')));
 assert(rows[0].includes('Active delegated reviewer: actor:delegate'));
 assert(rows[1].includes('Delegation revoked'));assert(rows[1].includes("original owner's authority is checked"));assert(!rows[1].includes('actor:delegate'));
 for(const index of [2,3,4,5]){assert(rows[index].includes('Decision by: actor:historical'));assert(!rows[index].includes('actor:delegate'));}
 assert(rows[5].includes('result recovery required'));assert(!rows[5].includes('Awaiting review'));
 assert(rows[6].includes('Review through the recovery group'));assert(!rows[6].includes('actor:delegate'));
 assert(!rows[7].includes('actor:delegate'));assert(rows[7].includes('Responsibility changed; submit a new proposal for the current owner'));
 assert(rows[8].includes('Inspect the invalid basis'));assert(!rows[8].includes('actor:delegate'));
 assert(!rows[9].includes('Decision by:'));assert(!rows[9].includes('actor:delegate'));
 assert(rows[10].includes('Delegation expired'));assert(!rows[10].includes('actor:delegate'));
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))
