"""Project real proposal receipts into one visible authority sequence."""

from __future__ import annotations

import json

import pytest

from orgrebase.workspace.change_proposals import change_detail
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_continuous_changes import ReviewClock, make_service, proposal
from tests.workspace.test_workspace_client import run_node


@pytest.fixture(scope="module")
def records(tmp_path_factory):
    root = tmp_path_factory.mktemp("change-progress")
    clock = ReviewClock()
    service = make_service(root / "workspace.sqlite", review_clock=clock)
    configure(service, root / "native")
    try:
        event = proposal(service, "progress-visible", "product_plan", "Enterprise reviewed")
        service.register_change(event)
        received = change_detail(service, event.event_id)
        preview = service.preview_change(event.event_id)
        previewed = change_detail(service, event.event_id)
        clock.advance()
        approval = service.approve_change(event.event_id, actor_id=event.owner_id,
                                         preview_digest=preview.preview.digest)
        approved = change_detail(service, event.event_id)
        service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
        return {"received": received, "previewed": previewed, "approved": approved,
                "applied": change_detail(service, event.event_id)}
    finally:
        service.close()


def run_progress(records, script):
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require('./tests/workspace/console_dom_harness.js');
const records=RECORDS,calls=[];let item=structuredClone(records.received);
window.OrgRebaseClient={session:()=>({mode:'local',principal:null}),async json(path,request={}){
 calls.push({path,request});
 if(path.endsWith('/change-options'))return {execution_run_id:item.execution_run_id,fields:[]};
 if(path.includes('/changes?'))return {items:[structuredClone(item)],next_cursor:null};
 return structuredClone(item);
}};
vm.runInNewContext(fs.readFileSync('demo/console/change-workbench.js','utf8'),
 {window,document,CustomEvent,performance,crypto,setTimeout,clearTimeout});
const refresh=()=>window.OrgRebaseChangeWorkbench.refresh();
const progress=()=>window.OrgRebaseChangeWorkbench.authorityProgress();
const phases=()=>Array.from(progress().steps,step=>step.status);
(async()=>{await tick();await tick();
SCRIPT
assert.equal(calls.filter(call=>call.request.method==='POST').length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("RECORDS", json.dumps(records)).replace("SCRIPT", script))


def test_real_native_preview_approval_and_effect_remain_separate(records):
    run_progress(records, r'''
assert.deepEqual(phases(),['complete','unobserved','waiting','waiting','waiting','waiting']);
item=structuredClone(records.previewed);await refresh();
assert.deepEqual(phases(),['complete','complete','complete','complete','waiting','waiting']);
item=structuredClone(records.approved);await refresh();
assert.deepEqual(phases(),['complete','complete','complete','complete','complete','waiting']);
item=structuredClone(records.applied);await refresh();
assert.deepEqual(phases(),Array(6).fill('complete'));
assert(progress().steps[5].text.includes(item.outcome.outcome.quote.version));
document.documentElement.lang='zh';assert(progress().steps[3].label==='独立契约复核');
''')


def test_mismatched_receipts_cannot_complete_another_authority_stage(records):
    run_progress(records, r'''
for(const [mutate,stage] of [
 [value=>value.preview.native_execution.binding.nonce='other',1],
 [value=>value.preview.native_execution.binding.change_set_ref+='-other',1],
 [value=>value.preview.bundle.advisory.ingestion_receipt.nonce='other',2],
 [value=>value.preview.bundle.advisory.ingestion_receipt.decisions[0].decision='REJECTED',2],
 [value=>value.preview.bundle.advisory.coordination_receipt.workflow_run_id='other',3],
 [value=>value.preview.native_execution.actions=value.preview.native_execution.actions.filter(action=>action.action!=='accept_task_result'),3],
 [value=>value.approval.binding.change_kind='other',4],
 [value=>value.approval.approval_review_evidence.review_wait_satisfied=false,4],
 [value=>value.outcome.outcome.workspace_rebase_receipt.base_rebase_receipt_digest='other',5],
]){item=structuredClone(records.applied);mutate(item);await refresh();assert.notEqual(phases()[stage],'complete');}
item=structuredClone(records.applied);item.preview.native_execution.binding.nonce='other';await refresh();
assert.notEqual(phases()[3],'complete','broken native evidence must not downgrade to reference review');
''')


def test_rejection_and_recovery_are_visible_without_claiming_verified_effect(records):
    run_progress(records, r'''
// Controlled projection cases; they are not represented as backend execution evidence.
item=structuredClone(records.previewed);item.status='REJECTED';
item.rejection={actor_id:'owner:test',reason:'Required evidence missing'};await refresh();
assert.equal(phases()[4],'blocked');assert.equal(phases()[5],'blocked');
assert(progress().steps[4].text.includes('Rejected'));
item=structuredClone(records.approved);item.status='RECOVERY_REQUIRED';await refresh();
assert.equal(phases()[5],'current');assert(progress().steps[5].text.includes('recovery required'));
window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));
assert.equal(progress(),null);
''')
