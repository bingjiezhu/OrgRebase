"""A zero-change Quote is a verified baseline, never two imaginary approvals."""
import json

from tests.workspace.test_workspace_client import CONSOLE, run_node


def test_zero_change_baseline_requires_same_run_formation_and_archive_bindings():
    source = (CONSOLE / "app.js").read_text()
    archive = source[source.index("function currentRunArchiveProof("):source.index("const verifiedCompletionProjections")]
    projection = source[source.index("function currentRunValueProjection("):source.index("function currentRunValueCard(")]
    run_node(r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const d=c=>'sha256:'+c.repeat(64),run='run:baseline';
const archive={schema_version:'orgrebase.workspace-current-run-archive-view.v2',status:'ARCHIVED',run_id:run,
 business_complete:true,failures:[],claim_boundary:'CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING',record:{
 archive_class:'CURRENT_BUSINESS_RUN_COMPLETION',same_run_as_current_task:true,terminal_status:'COMPLETED',run_id:run,
 business_complete:true,human_approval_count:0,change_dispositions:[],selective_rebase_receipts:[],
 quote:{ref:'quote@v1',digest:d('a')},quote_event_count:4,canonical_authority:'ORGREBASE_CONTROL_PLANE',evidence_class:'VERIFIED_SAME_RUN_CANONICAL_STATE'}};
const state={schema_version:'orgrebase.workspace-state.v2',stage:'CURRENT',business_complete:true,execution:{run_id:run},
 change_history:{total:0,pending:0},changes:{},change_events:[],quote:{id:'quote',version:'v1',digest:d('a')},formation:{digest:d('b')},
 task_intake:{schema_version:'orgrebase.workspace-task-intake-run-receipt.v1',status:'FORMATION_COMPLETED',run_id:run,quote_ref:'quote@v1',
 intake_persisted:true,intake_canonical_target_writes:0,formation_authority:'ORGREBASE_CONTROL_PLANE',digest:d('c'),candidate_digest:d('d'),approval_digest:d('e'),formation_receipt_digest:d('b'),artifact_payload_digest:d('f'),event_digest:d('1')},
 event_scopes:{quote_business:{status:'PASS',events:4}}};
const context={stateExecution:s=>s.execution,activeCompetition:()=>null,isSha256Digest:v=>typeof v==='string'&&/^sha256:[a-f0-9]{64}$/.test(v),currentRunArchive:archive};vm.createContext(context);vm.runInContext(__SOURCE__,context);
assert(context.currentRunArchiveProof(archive,run));
assert.equal(context.currentRunValueProjection(state).status,'BASELINE_VERIFIED');
for(const mutate of [s=>s.task_intake.run_id='run:other',s=>s.task_intake.quote_ref='other@v1',s=>s.formation.digest=d('9'),s=>s.quote.digest=d('9'),s=>s.quote.version='v2',s=>s.event_scopes.quote_business.events=3,s=>s.event_scopes.quote_business.status='FAIL',s=>s.task_intake.intake_persisted=false,s=>s.change_history.pending=1,s=>s.task_intake.event_digest=null]){
 const value=structuredClone(state);mutate(value);assert.notEqual(context.currentRunValueProjection(value).status,'BASELINE_VERIFIED');
}
for(const mutate of [a=>a.record.human_approval_count=1,a=>a.record.quote.ref='quote@v2',a=>a.record.business_complete=false,a=>a.failures=['BROKEN'],a=>a.record.change_dispositions=[{status:'APPLIED'}],a=>a.record.quote_event_count=0,a=>a.run_id='run:other']){
 const value=structuredClone(archive);mutate(value);assert.equal(context.currentRunArchiveProof(value,run),false);
}
'''.replace("__SOURCE__", json.dumps(archive + "\n" + projection)))
