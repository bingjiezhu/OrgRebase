from __future__ import annotations

import json
from pathlib import Path

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


def test_source_readmission_console_binds_exact_sources_owners_and_never_auto_applies():
    source = json.dumps((ROOT / "demo/console/source-readmission.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);
const calls=[],items=[];let failOnce=true;
const candidates=[{event_id:'date-1',event_digest:'sha256:date',slot_id:'launch_date',value:'2027-01-01',owner_id:'owner:product'}, {event_id:'currency-1',event_digest:'sha256:currency',slot_id:'currency',value:'USD',owner_id:'owner:finance'}];
window.OrgRebaseClient={session:()=>({mode:'local'}),async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path==='/api/workspace/source-readmission-options')return {schema_version:'orgrebase.source-readmission-options.v1',candidates:structuredClone(candidates),allowed_actions:['CREATE']};
 if(path.startsWith('/api/workspace/source-readmission-groups?'))return {items:structuredClone(items),next_cursor:null};
 if(path==='/api/workspace/source-readmission-groups'){
  if(!items.length)items.push({schema_version:'orgrebase.source-readmission-group-detail.v1',group:{id:request.body.group_id,digest:'sha256:group',preview:{digest:'sha256:preview',results:[{object_id:'quote',classification:'AFFECTED_HARD'},{object_id:'unresolved-work',classification:'UNKNOWN'}]},reason:request.body.reason,predecessor_ref:'quote@v3'},source_events:candidates.map(item=>({...item,base_version:'v1',proposal:{version:'p2',payload:{canonical_value:item.value}}})),state:'REVIEW',owners:candidates.map(item=>({owner_id:item.owner_id,source_ids:[item.slot_id],allowed_actions:['APPROVE','REJECT'],source_approval:null})),allowed_actions:[],review_remaining_ms:0});
  if(failOnce){failOnce=false;throw new Error('RESPONSE_LOST');}return structuredClone(items[0]);
 }
 if(path.endsWith('/approve')){
  const body=request.body;assert.equal(body.group_digest,'sha256:group');assert.equal(body.preview_digest,'sha256:preview');assert.equal(body.actor_id,body.owner_id);
  const owner=items[0].owners.find(item=>item.owner_id===body.owner_id);owner.source_approval={approval:{actor_id:body.actor_id}};owner.allowed_actions=['REJECT'];
  if(items[0].owners.every(item=>item.source_approval)){items[0].state='APPROVED';items[0].allowed_actions=['APPLY'];}return structuredClone(items[0]);
 }
 if(path.endsWith('/apply')){items[0].state='APPLIED';items[0].allowed_actions=[];items[0].outcome={quote:{id:'quote',version:'v4'}};return structuredClone(items[0]);}
 return structuredClone(items[0]);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=> 'stable-group-id'}});
(async()=>{
 await tick();await tick();
 assert.equal(nodes.get('source-group-create').disabled,true);
 for(const id of ['date-1','currency-1']){const item=nodes.get('source-group-choice-'+id);item.checked=true;await item.emit('change');}
 assert.equal(nodes.get('source-group-create').disabled,false);
 nodes.get('source-group-reason').value='Restore two independently owned sources';await nodes.get('source-group-reason').emit('input');
 await nodes.get('source-group-form').emit('submit');await nodes.get('source-group-form').emit('submit');await tick();
 const create=calls.filter(item=>item.path==='/api/workspace/source-readmission-groups');
 assert.equal(create.length,1,'uncertain creation cannot be submitted again before reading its record');
 await window.OrgRebaseSourceReadmission.refresh();await tick();
 assert.equal(calls.filter(item=>item.path==='/api/workspace/source-readmission-groups').length,1,'refresh recovers the persisted group without replay');
 assert.deepEqual(JSON.parse(JSON.stringify(create[0].request.body.events)),candidates.map(item=>({event_id:item.event_id,event_digest:item.event_digest})));
 const shown=element=>[element.textContent,...element.children.map(shown)].join(' ');
 assert(shown(nodes.get('source-group-detail')).includes('Insufficient evidence; hold for review'));
 assert(shown(nodes.get('source-group-detail')).includes('quote · Rebuild'));
 assert.equal(button('Approve my source scope').disabled,true);
 let ack=nodes.get('source-group-ack');ack.checked=true;await ack.emit('change');assert.equal(document.activeElement,ack);
 await button('Approve my source scope').emit('click');await tick();
 assert.equal(items[0].state,'REVIEW');assert.equal(calls.filter(item=>item.path.endsWith('/apply')).length,0);
 ack=nodes.get('source-group-ack');ack.checked=true;await ack.emit('change');await button('Approve my source scope').emit('click');await tick();
 assert.equal(items[0].state,'APPROVED');assert.equal(calls.filter(item=>item.path.endsWith('/apply')).length,0,'all approvals never auto-apply');
 await button('Restore sources and rebuild one quote').emit('click');await tick();
 assert.equal(items[0].state,'APPLIED');assert.equal(calls.filter(item=>item.path.endsWith('/apply')).length,1);
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));
 assert.equal(nodes.get('source-group-list').children.length,0);assert.equal(nodes.get('source-group-create').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('SOURCE', source).replace('HARNESS', harness))


def test_source_readmission_asset_loads_after_shared_client():
    html = (ROOT / "demo/console/index.html").read_text()
    assert html.index('/assets/workspace-client.js') < html.index('/assets/source-readmission.js')
    assert html.index('/assets/change-workbench.js') < html.index('/assets/source-readmission.js')


def test_failed_group_creation_retains_the_exact_attempt_and_reads_usage_without_replay():
    source = json.dumps((ROOT / "demo/console/source-readmission.js").read_text())
    changes = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS),calls=[];
const candidates=[{event_id:'date',event_digest:'sha256:date',slot_id:'launch_date',value:'2027-01-01',owner_id:'product'},{event_id:'currency',event_digest:'sha256:currency',slot_id:'currency',value:'USD',owner_id:'finance'}];
let delayed=false,resolve;
const attempt={group_id:'recovery:one',advisory_attempt:{state:'RESULT_UNKNOWN',usage_status:'UNKNOWN',public_error_code:'OPENAI_TIMEOUT',cost_reservation:{scope:'ONE_PREVIEW_ATTEMPT',currency:'USD',reserved_microusd:12500,limit_microusd:50000},receipt_summaries:[{dispatch_state:'SENT_UNKNOWN',provider_request_id:null,input_tokens:null,output_tokens:null}]}};
window.OrgRebaseClient={session:()=>({mode:'oidc'}),async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path.endsWith('/change-options'))return {execution_run_id:'run:one',fields:[]};
 if(path.includes('/changes?'))return {items:[],next_cursor:null};
 if(path.endsWith('/source-readmission-options'))return {schema_version:'orgrebase.source-readmission-options.v1',candidates,allowed_actions:['CREATE']};
 if(path.includes('/source-readmission-groups?'))return {items:[],next_cursor:null};
 if(path.endsWith('/attempt')){assert.equal(path,'/api/workspace/source-readmission-groups/recovery%3Aone/attempt');return delayed?new Promise(done=>{resolve=()=>done(structuredClone(attempt))}):structuredClone(attempt);}
 if(request.method==='POST')throw Object.assign(new Error('WORKSPACE_ADVISORY_RESULT_UNKNOWN'),{code:'WORKSPACE_ADVISORY_RESULT_UNKNOWN'});
 throw Object.assign(new Error('NOT_FOUND'),{status:404});
}};
vm.runInNewContext(CHANGES,{window,document,CustomEvent,performance,crypto});
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=> 'one'}});
const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');
(async()=>{
 await tick();await tick();for(const id of ['date','currency']){nodes.get('source-group-choice-'+id).checked=true;await nodes.get('source-group-choice-'+id).emit('change');}
 nodes.get('source-group-reason').value='Restore both sources';await nodes.get('source-group-form').emit('submit');
 assert.equal(nodes.get('source-group-attempt-id').textContent,'recovery:one');assert.equal(nodes.get('source-group-create').disabled,true);
 assert.equal(nodes.get('source-group-detail').children.length,0,'failed attempt is not a group');
 await button('Check this recovery attempt').emit('click');
 let summary=text(nodes.get('source-group-attempt-summary'));assert(summary.includes('USD 0.012500'));assert(summary.includes('Unknown; do not count as zero usage'));
 await window.OrgRebaseSourceReadmission.refresh();await nodes.get('source-group-form').emit('submit');
 assert.equal(calls.filter(item=>item.request.method==='POST').length,1,'refresh queries the attempt when the formal group is absent');
 assert(text(nodes.get('source-group-attempt-summary')).includes('SENT_UNKNOWN'));
 delayed=true;const pending=button('Check this recovery attempt').emit('click');await tick();
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));resolve();await pending;
 assert.equal(nodes.get('source-group-attempt').hidden,true);assert.equal(nodes.get('source-group-attempt-summary').children.length,0);
 assert.equal(nodes.get('source-group-create').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('SOURCE', source).replace('CHANGES', changes).replace('HARNESS', harness))


def test_browser_recovery_proof_verifies_current_archive_and_rejects_scope_substitution(tmp_path):
    from orgrebase.api import _workspace_current_run_archive_view
    from orgrebase.workspace.source_readmission import apply_group, approve_group
    from tests.workspace.test_continuous_changes import ReviewClock, make_service
    from tests.workspace.test_source_readmission_groups import command, prepare
    clock = ReviewClock()
    service = make_service(tmp_path / "browser-proof.sqlite", review_clock=clock)
    try:
        detail = prepare(service)
        clock.advance()
        for owner in detail["owners"]:
            approve_group(service, detail["group"]["id"], command(detail, owner["owner_id"]))
        apply_group(service, detail["group"]["id"], command(detail))
        archive = _workspace_current_run_archive_view(service)
        assert archive["status"] == "ARCHIVED", archive["failures"]
        from orgrebase.workspace.completed_run_observability import (
            build_completed_run_observability_from_trusted_state,
        )
        state = service.state()
        observed = build_completed_run_observability_from_trusted_state({**state, **service.completion_history()}, archive)
        assert observed["status"] == "PROJECTED", observed["failures"]
        app = (ROOT / "demo/console/app.js").read_text()
        helpers = "\n".join((
            app[app.index("function receiptForChange"):app.index("const BUSINESS_CHANGE_OBJECT_KEYS")],
            app[app.index("function isObservedCount"):app.index("function currentRunValueCard")],
            app[app.index("const verifiedApprovalAuthorities"):app.index("function renderCurrentRunArchive")],
        ))
        run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm'),crypto=require('node:crypto').webcrypto;
const window={};const context={window,crypto,TextEncoder};context.globalThis=context;
vm.runInNewContext(__WIRE_SOURCE__,context);vm.runInNewContext(__PROOF_SOURCE__,context);
const archive=__VIEW__,receipt=archive.record.selective_rebase_receipts.at(-1);
const observed=__OBSERVED__,state=__STATE__;
vm.runInNewContext(__HELPERS__ + `
function isSha256Digest(value){return typeof value==='string'&&/^sha256:[0-9a-f]{64}$/.test(value);}
function activeCompetition(){return null;}function stateExecution(state){return state.execution;}
let currentRunArchive=null,currentRunObservability=null;
globalThis.check=async function(archive,observed,state){
 currentRunArchive=archive;currentRunObservability=observed;
 await verifyArchiveApprovalAuthorities(archive);
 if(completedRunObservabilityProjection(observed,archive,archive.run_id)!==null)throw new Error('UNVERIFIED_COMPLETION_QUALIFIED');
 await verifyCompletedRunObservability(observed,archive);
 const projection=completedRunObservabilityProjection(observed,archive,archive.run_id);
 if(projection?.status!=='PASS'||!projection.summary||projection.items.length!==3)throw new Error('CURRENT_SUMMARY_INVALID');
 if(currentRunValueProjection(state).status!=='PASS')throw new Error('CURRENT_GROUP_VALUE_INVALID');
 const bounded=structuredClone(state);bounded.change_events=bounded.change_events.slice(-1);
 bounded.changes=Object.fromEntries(bounded.change_events.map(item=>[item.event_id,state.changes[item.event_id]]));
 const limited=currentRunValueProjection(bounded);
 if(limited.status!=='PASS'||!limited.summary||limited.receiptCount!==3||limited.approvalCount!==4
   ||Object.hasOwn(limited,'rebuilt')||Object.hasOwn(limited,'decisions'))throw new Error('BOUNDED_HISTORY_FABRICATED_DETAILS');
 const pristine=structuredClone(observed);
 for(const mutate of [item=>item.otlp.resourceMetrics[0].scopeMetrics[0].metrics[0].gauge.dataPoints[0].asInt='999',
   item=>item.completion_binding.archive_wire.scheme='unknown',item=>item.completion_binding.final_quote_ref='other@v4']){
  const bad=structuredClone(pristine);mutate(bad);let rejected=false;
  try{await verifyCompletedRunObservability(bad,archive);}catch{rejected=true;}
  if(!rejected)throw new Error('RESIGNED_SUMMARY_NOT_REJECTED');
 }
 const changedArchive=structuredClone(archive);changedArchive.record.quote_event_count+=1;
 let rejected=false;try{await verifyCompletedRunObservability(pristine,changedArchive);}catch{rejected=true;}
 if(!rejected)throw new Error('OTHER_ARCHIVE_ACCEPTED');
 observed.otlp.resourceMetrics[0].scopeMetrics[0].metrics[0].gauge.dataPoints[0].asInt='888';
 if(completedRunObservabilityProjection(observed,archive,archive.run_id)!==null)throw new Error('MUTATED_CACHED_SUMMARY_ACCEPTED');
};`,{...context,structuredClone});
(async()=>{
 assert.equal(window.OrgRebaseSourceReadmissionProof.bound(receipt,archive.run_id,archive.record.change_dispositions),false,'unverified bytes cannot qualify');
 await window.OrgRebaseSourceReadmissionProof.verify(receipt);
 assert.equal(window.OrgRebaseSourceReadmissionProof.bound(receipt,archive.run_id,archive.record.change_dispositions),true);
 await context.check(archive,observed,state);
 for(const mutation of ['owner-scope','source-delta','review','quote-version','run','schema']){
  const fake=structuredClone(receipt),proof=fake.group_evidence;
  if(mutation==='owner-scope')proof.owners[0].source_ids=proof.owners[1].source_ids;
  else if(mutation==='source-delta')proof.source_events[0].proposal.payload.canonical_value='different';
  else if(mutation==='review')proof.owners[0].review_evidence.review_wait_satisfied=false;
  else if(mutation==='quote-version')proof.outcome.quote.version='v99';
  else if(mutation==='run')proof.group.execution_run_id='different-run';
  else proof.group.schema_version='unknown';
  fake.group_evidence_wire.digest=await window.OrgRebaseWire.digest(proof);
  await window.OrgRebaseSourceReadmissionProof.verify(fake);
  assert.equal(window.OrgRebaseSourceReadmissionProof.bound(fake,archive.run_id,archive.record.change_dispositions),false,mutation);
 }
 receipt.owner_approvals.pop();
 assert.equal(window.OrgRebaseSourceReadmissionProof.bound(receipt,archive.run_id,archive.record.change_dispositions),false,'mutation after verification invalidates cached proof');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('__WIRE_SOURCE__', json.dumps((ROOT / 'demo/console/wire.js').read_text()))
           .replace('__PROOF_SOURCE__', json.dumps((ROOT / 'demo/console/source-readmission-proof.js').read_text()))
           .replace('__VIEW__', json.dumps(archive))
           .replace('__HELPERS__', json.dumps(helpers))
           .replace('__OBSERVED__', json.dumps(observed))
           .replace('__STATE__', json.dumps(state)))
    finally:
        service.close()
