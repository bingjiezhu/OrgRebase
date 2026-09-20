from __future__ import annotations

import json
from contextlib import closing

import pytest

from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_workspace_client import ROOT, run_node


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_context_usage_is_scoped_private_and_distinguishes_unknown(tmp_path, language):
    with closing(WorkspaceService(review_duration_seconds=0)) as workspace:
        workspace.form_quote()
        preview = workspace.preview_change("launch_date")
        approval = workspace.approve_change(
            "launch_date", actor_id=workspace.change_owner["launch_date"], preview_digest=preview.preview.digest
        )
        workspace.apply_approved_change("launch_date", approval_digest=approval["approval_digest"])
        fixture = {"state": workspace.state(), "detail": change_detail(workspace, "launch_date")}
    path = tmp_path / "context.json"
    path.write_text(json.dumps(fixture))
    program = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
document.documentElement.lang=LANGUAGE;
const fixture=JSON.parse(fs.readFileSync(FIXTURE,'utf8')),original=fixture.detail;
let detail=structuredClone(original);const run=fixture.state.execution.run_id;
window.OrgRebaseClient={session:()=>null,workspace:()=>'default',async json(path){
 if(path.endsWith('/change-options'))return {execution_run_id:run,fields:[]};
 if(path.includes('/changes?'))return {items:[{event:detail.event,status:detail.status}],next_cursor:null};
 return structuredClone(detail);
}};
vm.runInNewContext(fs.readFileSync(SOURCE,'utf8'),{window,document,CustomEvent,performance,crypto});
const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');
const find=(node,id)=>node.id===id?node:node.children.map(child=>find(child,id)).find(Boolean);
const contexts=()=>detail.outcome.outcome.rebase_receipt.context_manifests;
async function reload(){await window.OrgRebaseChangeWorkbench.select(detail.event.event_id);return find(nodes.get('change-result'),'change-context-usage');}
(async()=>{
 await tick();await tick();window.dispatchEvent(new CustomEvent('orgrebase:staterendered',{detail:fixture.state}));
 let view=await reload();assert.equal(view.dataset.status,'RECORDED');
 const empty=LANGUAGE==='en'?'No exclusions recorded':'本次未记录排除项';assert(text(view).includes(empty));
 // These view inputs exercise display safety, not server-side evidence admission.
 const context=contexts()[0];context.included=[
  {disposition:'INCLUDED_AS_PREMISE',label:'Allowed projection',payload:{secret:'DO_NOT_DISPLAY'}},
  {disposition:'DERIVED',label:'Derived projection',payload:{secret:'DO_NOT_DISPLAY'}},
  {disposition:'FUTURE',label:'DO_NOT_DISPLAY',payload:{secret:'DO_NOT_DISPLAY'}}];
 context.excluded=[{disposition:'EXCLUDED',object_ref:'DO_NOT_DISPLAY',label:'DO_NOT_DISPLAY',reason_code:'DO_NOT_DISPLAY',payload:{secret:'DO_NOT_DISPLAY'}},
  {disposition:'EXCLUDED',object_ref:'DO_NOT_DISPLAY',reason_code:'__proto__',payload:null}];
 view=await reload();assert.equal(view.dataset.status,'RECORDED');assert(!text(view).includes('DO_NOT_DISPLAY'));
 assert(text(view).includes('Allowed projection'));assert(text(view).includes('Derived projection'));
 assert(text(view).includes(LANGUAGE==='en'?'Other recorded reason':'其他已记录原因'));
 assert(text(view).includes(LANGUAGE==='en'?'Unrecognized dispositions':'未识别的处置类型'));
 context.excluded=[{disposition:'FUTURE',object_ref:'DO_NOT_DISPLAY',reason_code:'DO_NOT_DISPLAY'}];
 view=await reload();assert(!text(view).includes(empty));assert(!text(view).includes('DO_NOT_DISPLAY'));
 assert(text(view).includes(LANGUAGE==='en'?'their meaning is unknown':'不能确认其含义'));
 contexts().push({...structuredClone(context),id:'context:second'});
 view=await reload();assert.equal(view.dataset.status,'RECORDED','same actor may have two distinct manifests');
 assert.equal(view.children.filter(child=>child.className==='change-context-record').length,2);
 for(const mutate of [
  c=>c.push(structuredClone(c[0])),c=>c[0].purpose='another-purpose',c=>c[0].graph_revision='another-graph',
  c=>c[0].policy_revision='another-policy',c=>c[0].target_object_id='another-target',
  c=>c[0].included=null,c=>c[0].excluded=null,c=>c[0].digest='not-a-digest']){
  detail=structuredClone(original);mutate(contexts());view=await reload();assert.equal(view.dataset.status,'INVALID');
  assert(!text(view).includes('Allowed projection'));
 }
 detail=structuredClone(original);contexts().length=0;assert.equal((await reload()).dataset.status,'NOT_RECORDED');
 detail=structuredClone(original);delete detail.outcome.outcome.rebase_receipt.context_manifests;
 assert.equal((await reload()).dataset.status,'NOT_RECORDED');
 detail=structuredClone(original);detail.outcome.outcome.rebase_receipt.workflow_run_id='another-run';
 assert.equal(await reload(),undefined,'foreign run cannot render a context summary');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    for key, value in {
        "HARNESS": str(ROOT / "tests/workspace/console_dom_harness.js"),
        "SOURCE": str(ROOT / "demo/console/change-workbench.js"),
        "FIXTURE": str(path),
        "LANGUAGE": language,
    }.items():
        program = program.replace(key, json.dumps(value))
    run_node(program)
