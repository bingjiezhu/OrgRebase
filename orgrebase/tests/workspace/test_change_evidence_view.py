from __future__ import annotations

import json
from pathlib import Path

from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


def test_selected_change_uses_its_actual_receipts_and_predecessor(tmp_path: Path):
    workspace = WorkspaceService(store_path=tmp_path / "change-evidence.sqlite")
    try:
        workspace.form_quote()
        for kind, actor in (("launch_date", "human:product-owner"), ("currency", "human:finance-owner")):
            preview = workspace.preview_command(kind)
            approval = workspace.approve_change(kind, actor_id=actor, preview_digest=preview["preview_digest"])
            workspace.apply_approved_change(kind, approval_digest=approval["approval_digest"])
        state = workspace.state()
        observed = {"detail": change_detail(workspace, "launch_date"), "state": {
            "quote": state["quote"], "execution": state["execution"],
            "enterprise_data_lineage": {
                key: state["enterprise_data_lineage"][key] for key in ("run_id", "quotes")
            },
        }}
    finally:
        workspace.close()

    script = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS),calls=[];
const observed=__OBSERVED__;
let detail=structuredClone(observed.detail);
window.OrgRebaseClient={session:()=>({mode:'local',principal:null}),async json(path,request={}){
 calls.push({path,request});
 if(path.endsWith('/change-options'))return {execution_run_id:detail.execution_run_id,fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(detail)],next_cursor:null};
 return structuredClone(detail);
}};
vm.runInNewContext(__APP_SOURCE__,{window,document,CustomEvent,performance,crypto});
const walk=node=>node?[node,...node.children.flatMap(walk)]:[];
const text=node=>walk(node).map(item=>item.textContent||'').join('\n');
const section=()=>nodes.get('change-result');
const table=()=>walk(section()).find(node=>node.id==='change-quote-comparison');
const rows=()=>walk(table()).filter(node=>node.dataset.field);
const state=value=>window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:value}));
const reload=()=>window.OrgRebaseChangeWorkbench.select(detail.event.event_id);
(async()=>{
 await tick();await tick();
 assert.equal(section().dataset.evidenceStatus,'BOUND','real committed records satisfy the display binding');
 assert.equal(table(),undefined,'without an exact predecessor, do not invent a comparison');
 state(observed.state);
 assert.equal(observed.state.quote.version,'v3');
 assert.equal(rows().length,10);
 assert.equal(rows().filter(node=>node.dataset.result==='CHANGED').length,1);
 const launch=rows().find(node=>node.dataset.field==='launch_date');
 assert.equal(launch.dataset.result,'CHANGED');
 const currency=rows().find(node=>node.dataset.field==='currency');
 assert.equal(currency.dataset.result,'PRESERVED');
 assert.equal(currency.children[1].textContent,'USD');assert.equal(currency.children[2].textContent,'USD');
 assert(!text(section()).includes('EUR'),'later currency change must not appear in launch change history');
 const heads=walk(table()).filter(node=>node.tagName==='TH'&&node.getAttribute('scope')==='col');
 assert.deepEqual(heads.map(node=>node.textContent),['Field','Before v1','After v2','Result']);
 document.documentElement.lang='zh-CN';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
 assert.deepEqual(walk(table()).filter(node=>node.tagName==='TH'&&node.getAttribute('scope')==='col')
   .map(node=>node.textContent),['字段','变更前 v1','变更后 v2','结果']);
 document.documentElement.lang='en';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
 assert(text(section()).includes(detail.outcome.outcome.workspace_rebase_receipt.committed_at));
 for(const agent of detail.outcome.outcome.rebase_receipt.agent_runs){
   assert(text(section()).includes(agent.task_id));assert(text(section()).includes(agent.trace_id));
   for(const skill of agent.skill_versions)assert(text(section()).includes(skill));
 }
 const firstTable=table();nodes.get('change-lineage').open=true;state(observed.state);
 assert.equal(table(),firstTable,'unchanged polling preserves the existing DOM');
 assert.equal(nodes.get('change-lineage').open,true,'refresh retains expanded trace');
 const switched=structuredClone(observed.state);switched.execution.run_id='run:other';state(switched);
 assert.equal(table(),undefined,'another run cannot supply predecessor history');
 assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail(),null,'a different run retires the old detail');
 await tick();await tick();
 state(observed.state);assert.equal(await window.OrgRebaseChangeWorkbench.refresh(),true);
 assert.equal(window.OrgRebaseChangeWorkbench.selectedDetail().execution_run_id,observed.detail.execution_run_id);
 assert.equal(rows().length,10,'restore the current run before checking incomplete predecessor evidence');
 for(const mutate of [
   value=>value.enterprise_data_lineage.quotes.versions.splice(0,1),
   value=>value.enterprise_data_lineage.quotes.versions[0].digest='sha256:wrong',
   value=>value.enterprise_data_lineage.quotes.versions.push(structuredClone(value.enterprise_data_lineage.quotes.versions[0])),
   value=>delete value.enterprise_data_lineage.quotes.versions[0].payload.notice_required,
 ]){
   const incomplete=structuredClone(observed.state);mutate(incomplete);state(incomplete);
   assert.equal(table(),undefined,'missing or ambiguous predecessor never falls back to current versions');
   assert(text(section()).includes('history window'));
 }
 state(observed.state);
 for(const mutate of [
   value=>value.outcome.outcome.preview_digest='sha256:other',
   value=>value.outcome.outcome.rebase_receipt.workflow_run_id='run:other',
   value=>value.outcome.outcome.rebase_receipt.run_nonce='nonce:other',
   value=>value.outcome.outcome.workspace_rebase_receipt.base_rebase_receipt_digest='sha256:other',
   value=>value.outcome.outcome.workspace_rebase_receipt.successor_object_refs=['work:quote@v3'],
   value=>value.outcome.outcome.approval_digest='sha256:other',
   value=>value.outcome.kind='event:other',
   value=>value.status='APPROVED',
 ]){
   detail=structuredClone(observed.detail);mutate(detail);await reload();
   assert.equal(section().dataset.evidenceStatus,'INCOMPLETE');assert.equal(table(),undefined);
 }
 detail=structuredClone(observed.detail);
 const markup='<img src=x onerror="approve()">';detail.event.proposal.source_refs=[markup];
 detail.outcome.outcome.rebase_receipt.context_manifests[0].included=[{payload:{private_value:'NEVER_RENDER_CONTEXT_CONTENT'}}];
 await reload();
 assert(text(section()).includes(markup),'source references are rendered as literal text');
 assert(!walk(section()).some(node=>node.tagName==='IMG'));
 assert(!text(section()).includes('NEVER_RENDER_CONTEXT_CONTENT'));
 document.documentElement.lang='zh';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
 assert.equal(rows().length,10);assert(text(table()).includes('字段值一致'));
 assert(!text(section()).includes('逐字节'));
 assert.equal(calls.filter(call=>call.request.method==='POST').length,0,'reading and rendering effects never executes a command');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    run_node(script.replace("HARNESS", json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js")))
             .replace("__APP_SOURCE__", json.dumps((ROOT / "demo/console/change-workbench.js").read_text()))
             .replace("__OBSERVED__", json.dumps(observed)))


def test_state_progress_refreshes_selected_change_once_without_overwriting_new_selection():
    script = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS),reads=[],calls=[];
const make=(id,slot,digit)=>({execution_run_id:'run:one',status:'RECEIVED',allowed_actions:['REJECT'],
 event:{event_id:id,digest:'sha256:'+digit.repeat(64),slot_id:slot,owner_id:'owner:one',base_version:'v1',base_digest:'sha256:'+'d'.repeat(64),
 proposal:{id:'claim:'+slot,version:'v2',payload:{canonical_value:'new'},source_refs:['source:one']}}});
const a=make('event:a','launch_date','a'),b=make('event:b','currency','b');
const pending=new Map();let defer=false;
window.OrgRebaseClient={session:()=>({mode:'local',principal:null}),async json(path,request={}){
 calls.push({path,request});
 if(path.endsWith('/change-options'))return {execution_run_id:'run:one',fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(a),structuredClone(b)],next_cursor:null};
 const item=decodeURIComponent(path.split('/').at(-1))==='event:a'?a:b;reads.push(item.event.event_id);
 return defer?new Promise(resolve=>pending.set(item.event.event_id,()=>resolve(structuredClone(item)))):structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto});
const dispatch=(override={})=>window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:{
 execution:{run_id:'run:one'},change_events:[a,b].map(item=>({event_id:item.event.event_id,event_digest:item.event.digest,status:item.status})),...override}}));
const selected=()=>window.OrgRebaseChangeWorkbench.selectedDetail();
(async()=>{
 await tick();await tick();assert.deepEqual(reads,['event:a']);
 dispatch();dispatch();await tick();assert.equal(reads.length,1,'unchanged polling adds no detail requests');
 nodes.get('change-detail-reason').value='Keep my review note';
 a.status='PREVIEWED';defer=true;dispatch();dispatch();await tick();
 assert.deepEqual(reads,['event:a','event:a'],'state progress starts one read, repeated polling does not cancel it');
 pending.get('event:a')();await tick();await tick();
 assert.equal(selected().status,'PREVIEWED');assert.equal(nodes.get('change-detail-reason').value,'Keep my review note');
 dispatch();dispatch();await tick();assert.equal(reads.length,2);
 a.status='APPLIED';
 dispatch({change_events:[{event_id:'event:a',event_digest:'sha256:'+'c'.repeat(64),status:'APPLIED'}]});
 await tick();assert.equal(reads.length,2,'changed identity never refreshes the selected event');
 dispatch();await tick();assert.equal(reads.length,3);
 const switching=window.OrgRebaseChangeWorkbench.select('event:b');await tick();
 dispatch();dispatch();await tick();assert.equal(reads.length,4,'state polling does not supersede an explicit selection in flight');
 pending.get('event:b')();await switching;assert.equal(selected().event.event_id,'event:b');
 pending.get('event:a')();await tick();await tick();
 assert.equal(selected().event.event_id,'event:b','late refresh of prior selection cannot overwrite the new event');
 assert.equal(selected().status,'RECEIVED');assert.equal(nodes.get('change-detail-title').textContent,'Currency · Awaiting preview');
 dispatch();await tick();assert.equal(reads.length,4);
 b.status='PREVIEWED';dispatch();await tick();assert.equal(reads.length,5);
 dispatch({execution:{run_id:'run:other'}});
 assert.equal(selected(),null,'a foreign run immediately retires the previous detail and its actions');
 assert(!nodes.get('change-detail-body').querySelectorAll('button').some(node=>!node.disabled));
 pending.get('event:b')();await tick();await tick();
 assert.equal(selected(),null,'a late detail read cannot restore the previous run');
 assert.equal(reads.length,5,'a mismatched run cannot request a detail from the previous run');
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));
 assert.equal(selected(),null);
 assert.equal(calls.filter(call=>call.request.method==='POST').length,0,'progress synchronization only reads details');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    run_node(script.replace("HARNESS", json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js")))
             .replace("SOURCE", json.dumps((ROOT / "demo/console/change-workbench.js").read_text())))
