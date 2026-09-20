"""Task detail UI uses a projected real receipt; unrelated private payload stays private."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/workspace/change-task-detail.json"


def check(mutation: str, assertion: str) -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
const item=JSON.parse(fs.readFileSync(FIXTURE,'utf8')).detail;
item.allowed_actions=[];
const advisory=item.preview.bundle.advisory,native=item.preview.native_execution;
MUTATION
const calls=[];
window.OrgRebaseClient={session:()=>({mode:'local',principal:null}),workspace:()=>'workspace:one',async json(path,request={}){
 calls.push({path,request});
 if(path.endsWith('/change-options'))return {execution_run_id:item.execution_run_id,fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};
 return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto,setTimeout,clearTimeout});
const text=node=>node?[node.textContent||'',...node.children.map(text)].join(' '):'';
(async()=>{
 await tick();await tick();
 const tasks=nodes.get('change-agentteams-tasks');
 const rows=tasks?.children.find(node=>node.tagName==='OL')?.children||[];
 const finance=rows.find(node=>node.dataset.nativeTask===native.tasks[1].task_id);
 const review=rows.find(node=>node.dataset.nativeTask===native.tasks[3].task_id);
 ASSERTION
 assert.equal(calls.filter(call=>call.request.method==='POST').length,0);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js")))
         .replace('FIXTURE', json.dumps(str(FIXTURE)))
         .replace('SOURCE', json.dumps((ROOT / "demo/console/change-workbench.js").read_text()))
         .replace('MUTATION', mutation).replace('ASSERTION', assertion))


def test_real_change_task_details_expose_scopes_handoffs_and_receipts_without_raw_payload():
    check("advisory.handoffs[1].payload.private_notes='PRIVATE_RAW_CANDIDATE'; advisory.orchestration_plan.tasks[1].required_capabilities=['INVENTED_SUCCESSFUL_TOOL'];", r'''
 assert.equal(rows.length,4);
 for(const row of rows){
  assert.equal(row.children[0].tagName,'DETAILS');
  assert.equal(row.children[0].dataset.changeTask,row.dataset.nativeTask);
  const phases=row.children[0].children.find(node=>node.tagName==='OL');
  assert.equal(phases.children.length,5);
  assert(phases.children.every(node=>text(node).includes('Recorded')));
 }
 assert(text(finance).includes('policy:finance.pricing'));
 assert(text(finance).includes('Rule-change explanation'));
 assert(text(finance).includes('orgrebase-control-plane'));
 assert(text(finance).includes('recorded target writes 0'));
 assert(text(finance).includes('capability requirements or version references are not invocation receipts'));
 assert(!text(tasks).includes('PRIVATE_RAW_CANDIDATE'));
 assert(!text(tasks).includes('INVENTED_SUCCESSFUL_TOOL'));
 assert(text(review).includes('DETERMINISTIC_CONTRACT_REVIEW'));
 assert(text(review).includes('not a cloud-model invocation or human approval'));
 assert(text(review).includes('3 exactly bound candidate handoffs'));
''')


@pytest.mark.parametrize("field", ['run_id', 'change_set_digest', 'plan_digest', 'preview_digest', 'nonce', 'change_set_ref'])
def test_foreign_native_binding_never_borrows_task_details(field):
    check(f"native.binding[{json.dumps(field)}]='foreign-binding';", "assert(!tasks);")


@pytest.mark.parametrize("mutation", [
    "advisory.handoffs[1].task_id='foreign-task';",
    "advisory.handoffs[1].from_agent='legal-steward';",
    "advisory.handoffs[1].delegation_task_digest='sha256:foreign';",
    "advisory.handoffs[1].workflow_run_id='foreign-run';",
    "advisory.handoffs[1].run_nonce='foreign-nonce';",
    "advisory.handoffs[1].change_set_id='foreign-change';",
    "advisory.agent_runs[1].agent_name='legal-steward';",
    "advisory.native_execution.results[1].agent_run_digest='sha256:foreign';",
    "advisory.ingestion_receipt.decisions[1].producer_worker='foreign-worker';",
    "advisory.ingestion_receipt.decisions[1].artifact_digest='sha256:foreign';",
    "advisory.handoffs.push(structuredClone(advisory.handoffs[1]));",
    "advisory.handoffs[1].payload.target_writes=1;",
    "advisory.handoffs[1].payload.input_refs=[];",
    "advisory.orchestration_plan.tasks[1].depends_on=[];",
    "delete advisory.ingestion_receipt.rejected_candidate_digests;",
])
def test_missing_or_misbound_candidate_is_explicitly_unavailable(mutation):
    check(mutation, r'''
 assert.equal(rows.length,4);
 assert(text(finance).includes('Not recorded or not bound to this task'));
 assert(!text(finance).includes('recorded target writes 0'));
 assert(!text(finance).includes('Rule-change explanation'));
 assert(text(review).includes('Not recorded or not bound to this task'));
''')


def test_missing_phase_receipt_is_not_invented_from_completed_status():
    check("native.tasks[1].action_receipt_digests=[];", r'''
 const phases=finance.children[0].children.find(node=>node.tagName==='OL');
 assert.equal(phases.children.length,5);
 assert(phases.children.every(node=>text(node).includes('Not recorded')));
 assert(!text(finance.children[0].children[0]).includes('Acceptance'));
''')


def test_projected_phase_cannot_borrow_another_action_digest():
    check("const action=native.actions.find(item=>item.key===`${native.tasks[1].task_id}:accept`); action.digest='sha256:foreign'; native.tasks[1].action_receipt_digests.push(action.digest);", r'''
 const phases=finance.children[0].children.find(node=>node.tagName==='OL');
 assert(text(phases.children[4]).includes('Not recorded'));
 assert(!text(finance.children[0].children[0]).includes('Acceptance'));
''')
