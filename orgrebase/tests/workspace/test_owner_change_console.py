from __future__ import annotations

import json
from pathlib import Path

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "demo/console"


def owner_console(program: str) -> None:
    setup = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(__HARNESS__);
const base='/api/workspace/organization/owner-changes', calls=[];
const oldOwner={issuer:'https://identity.example',subject:'old',actor_id:'owner:product'};
const newOwner={issuer:'https://identity.example',subject:'new',actor_id:'owner:successor'};
let session={mode:'oidc',authenticated:true,principal:{...oldOwner,tenant_id:'org:test'}};
let options={policy:'mutual-consent-v1',can_propose:true,blocked_reason:null,
 enterprise_binding_digest:'sha256:binding',snapshot_digest:'sha256:snapshot',
 resources:[{slot_id:'launch_date',label:'Launch date',owner_id:oldOwner.actor_id,object_id:'D1',domain_id:'product'}],
 eligible_owners:[oldOwner,newOwner]};
const coverage={status:'COMPLETE_WITHIN_RECORDED_SCOPE',inspected_count:0,returned_count:0,reason:'recorded_scope'};
function impact(body){return {schema_version:'orgrebase.owner-change-preview.v1',
 workspace_id:'default',organization_id:'org:test',requester:oldOwner,
 resource:options.resources[0],source:{ref:'D1@r1',digest:'sha256:source'},reason:body.reason,
 proposed_owner:body.proposed_owner,binding_digest:body.expected_binding_digest,snapshot_digest:body.expected_snapshot_digest,
 evaluated_at:'2026-09-12T00:00:00Z',dependent_work:[],dependency_coverage:coverage,
 pending_events:[],pending_event_coverage:coverage,unresolved_effects:[],effect_coverage:coverage,
 activation_allowed:false,database_writes:0};}
function detail(id='handover', actions=['CONFIRM_AUTHORIZATION']){return {
 proposal:{proposal_id:id,preview:impact({proposed_owner:newOwner,expected_binding_digest:options.enterprise_binding_digest,expected_snapshot_digest:options.snapshot_digest,reason:'Team handover'}),expires_at:'2026-09-12T01:00:00Z'},
 proposal_digest:'sha256:proposal',authorization:null,acceptance:null,activation:null,
 status:'PENDING_CONFIRMATION',blocked_reason:null,allowed_actions:actions,
 confirmation_role:actions[0]==='CONFIRM_AUTHORIZATION'?'authorization':'acceptance',actor_identity:session.principal};}
const saved=new Map(); let page=[], nextCursor=null, intercept=null;
window.OrgRebaseClient={session:()=>session,async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(intercept){const result=await intercept(path,request);if(result!==undefined)return result;}
 if(path.endsWith('/owner-change-options'))return structuredClone(options);
 if(path.startsWith(base+'?'))return {items:structuredClone(page),next_cursor:nextCursor};
 if(path.endsWith('/owner-change-preview'))return impact(request.body);
 if(path===base&&request.method==='POST'){
  const value=detail(request.body.proposal_id);value.proposal.preview=impact(request.body);saved.set(request.body.proposal_id,value);return structuredClone(value);
 }
 if(path.startsWith(base+'/')&&!request.method){
  const id=decodeURIComponent(path.slice(base.length+1));if(!saved.has(id))throw Object.assign(new Error('NOT_FOUND'),{status:404});return structuredClone(saved.get(id));
 }
 throw new Error('Unexpected request '+path);
}};
vm.runInNewContext(__SOURCE__,{window,document,CustomEvent,crypto});
const writes=()=>calls.filter(call=>call.request.method==='POST'&&!call.path.endsWith('/owner-change-preview'));
const text=element=>[element.textContent||'',...element.children.map(text)].join(' ');
async function fill(){
 await window.OrgRebaseOwnerChange.refresh();
 nodes.get('owner-change-resource').value='launch_date';await nodes.get('owner-change-resource').emit('change');
 const select=nodes.get('owner-change-successor');assert.equal(select.children.length,2,'current owner is not a successor');
 select.value=select.children[1].value;await select.emit('change');
 nodes.get('owner-change-reason').value='Team handover';await nodes.get('owner-change-reason').emit('input');
}
async function open(id='handover'){nodes.get('owner-change-open-id').value=id;await nodes.get('owner-change-open-form').emit('submit');}
async function acknowledge(){const ack=nodes.get('owner-change-ack');ack.checked=true;await ack.emit('change');}
'''
    run_node((setup + "\n(async()=>{\n" + program + "\n})().catch(error=>{console.error(error);process.exitCode=1});")
             .replace("__SOURCE__", json.dumps((CONSOLE / "owner-change.js").read_text()))
             .replace("__HARNESS__", json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))))


def test_preview_binds_current_inputs_and_registration_never_confirms_or_activates():
    owner_console(r'''
await tick();assert.equal(calls.length,0,'closed panel does not fetch private records');
await fill();await nodes.get('owner-change-form').emit('submit');
assert.equal(writes().length,0);assert.equal(nodes.get('owner-change-create').disabled,false);
const previewCall=calls.find(call=>call.path.endsWith('/owner-change-preview'));
assert.deepEqual(previewCall.request.body,{slot_id:'launch_date',expected_binding_digest:'sha256:binding',expected_snapshot_digest:'sha256:snapshot',proposed_owner:newOwner,reason:'Team handover'});
nodes.get('owner-change-reason').value='Updated reason';await nodes.get('owner-change-reason').emit('input');
await nodes.get('owner-change-create').emit('click');assert.equal(writes().length,0,'changed draft invalidates preview');
await nodes.get('owner-change-form').emit('submit');await nodes.get('owner-change-create').emit('click');
assert.equal(writes().length,1);assert.equal(writes()[0].path,base);
assert.equal(writes()[0].request.body.reason,'Updated reason');
assert.equal(button('Authorize transfer of my responsibility').disabled,true,'formal confirmation needs a fresh acknowledgment');
assert(text(nodes.get('owner-change-detail-body')).includes(writes()[0].request.body.proposal_id));
assert.equal(nodes.get('owner-change-reason').value,'');
assert(!calls.some(call=>call.request.headers),'UI never sets an acting identity');
''')


def test_server_actions_require_two_independent_sessions_and_activation_is_explicit():
    owner_console(r'''
saved.set('handover',detail());page=[detail()];await window.OrgRebaseOwnerChange.refresh();await open();
intercept=async(path,request)=>{
 if(path.endsWith('/confirm')){
  assert.deepEqual(structuredClone(request.body),{proposal_digest:'sha256:proposal'});
  const value=saved.get('handover');
  if(session.principal.subject==='old'){value.authorization={identity:oldOwner};value.allowed_actions=[];}
  else {assert.equal(session.principal.subject,'new');value.acceptance={identity:newOwner};value.allowed_actions=['ACTIVATE'];}
  return structuredClone(value);
 }
 if(path.endsWith('/activate')){
  const value=saved.get('handover');assert(value.authorization&&value.acceptance);
  assert.deepEqual(structuredClone(request.body),{proposal_digest:'sha256:proposal'});
  value.activation={activated_at:'2026-09-12T00:30:00Z'};value.status='APPLIED';value.allowed_actions=[];return structuredClone(value);
 }
};
await button('Authorize transfer of my responsibility').emit('click');assert.equal(writes().length,0);
await acknowledge();await button('Authorize transfer of my responsibility').emit('click');
assert.equal(writes().length,1);assert(!calls.some(call=>call.path.endsWith('/activate')));
session={...session,principal:{...newOwner,tenant_id:'org:test'}};
window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
assert.equal(nodes.get('owner-change-detail-body').children.length,0,'identity change discards private previous view');
saved.get('handover').allowed_actions=['CONFIRM_ACCEPTANCE'];await window.OrgRebaseOwnerChange.refresh();await open();
assert.equal(button('Accept this responsibility').disabled,true);await acknowledge();await button('Accept this responsibility').emit('click');
assert.equal(writes().length,2);assert(text(nodes.get('owner-change-detail-body')).includes('Activate the jointly confirmed transfer'),text(nodes.get('owner-change-notice')));assert.equal(button('Activate the jointly confirmed transfer').disabled,true,'acceptance acknowledgment cannot authorize activation');
await acknowledge();await button('Activate the jointly confirmed transfer').emit('click');
assert.equal(writes().length,3);assert.equal(nodes.get('owner-change-detail-title').textContent,'Owner transfer activated');
assert(text(nodes.get('owner-change-detail-body')).includes('prior tasks require replanning'));
''')


def test_lost_registration_is_reconciled_by_id_without_automatic_replay():
    owner_console(r'''
await fill();await nodes.get('owner-change-form').emit('submit');
intercept=async(path,request)=>{if(path===base&&request.method==='POST'){
 const value=detail(request.body.proposal_id);saved.set(request.body.proposal_id,value);throw new Error('RESPONSE_LOST');
}};
await nodes.get('owner-change-create').emit('click');assert.equal(writes().length,1);
const id=writes()[0].request.body.proposal_id;
assert.equal(nodes.get('owner-change-reason').disabled,true);assert.equal(nodes.get('owner-change-retry').disabled,true);
await nodes.get('owner-change-retry').emit('click');assert.equal(writes().length,1);
intercept=null;await window.OrgRebaseOwnerChange.refresh();assert.equal(writes().length,1);
assert(calls.some(call=>call.path===base+'/'+encodeURIComponent(id)&&!call.request.method));
assert.equal(nodes.get('owner-change-retry').hidden,true);
assert(text(nodes.get('owner-change-detail-body')).includes(id));
''')


def test_missing_uncertain_registration_can_only_retry_the_exact_original_id_and_input():
    owner_console(r'''
await fill();await nodes.get('owner-change-form').emit('submit');
let lose=true;intercept=async(path,request)=>{if(path===base&&request.method==='POST'&&lose){lose=false;throw new Error('RESPONSE_LOST');}};
await nodes.get('owner-change-create').emit('click');const original=writes()[0].request.body;
await window.OrgRebaseOwnerChange.refresh();assert.equal(writes().length,1);
assert.equal(nodes.get('owner-change-retry').disabled,false);
nodes.get('owner-change-reason').value='Changed DOM input';await nodes.get('owner-change-reason').emit('input');
await nodes.get('owner-change-create').emit('click');assert.equal(writes().length,1,'cannot fork an uncertain proposal');
await nodes.get('owner-change-retry').emit('click');assert.equal(writes().length,2);
assert.deepEqual(writes()[1].request.body,original,'manual recovery retains ID and full original input');
''')


def test_reconfirmation_uses_latest_exact_digest_and_revoked_history_remains_readable():
    owner_console(r'''
saved.set('handover',detail());await window.OrgRebaseOwnerChange.refresh();await open();await acknowledge();
saved.get('handover').proposal_digest='sha256:new-proposal';await window.OrgRebaseOwnerChange.refresh();
assert.equal(button('Authorize transfer of my responsibility').disabled,true);
await acknowledge();intercept=async(path,request)=>{if(path.endsWith('/confirm')){assert.deepEqual(structuredClone(request.body),{proposal_digest:'sha256:new-proposal'});throw new Error('RESPONSE_LOST');}};
await button('Authorize transfer of my responsibility').emit('click');assert.equal(writes().length,1);
assert.equal(button('Authorize transfer of my responsibility').disabled,true);
await button('Authorize transfer of my responsibility').emit('click');assert.equal(writes().length,1);
intercept=null;saved.get('handover').blocked_reason='AUTH_WORKSPACE_DENIED';saved.get('handover').status='REPLAN_REQUIRED';saved.get('handover').allowed_actions=[];
await window.OrgRebaseOwnerChange.refresh();assert.equal(writes().length,1);
assert(text(nodes.get('owner-change-detail-body')).includes('AUTH_WORKSPACE_DENIED'));
assert(text(nodes.get('owner-change-detail-body')).includes('Team handover'));
assert.equal(nodes.get('owner-change-detail-title').textContent,'Expired; a new proposal is required');
''')


def test_logout_discards_late_private_reads_and_writes():
    owner_console(r'''
await fill();let resolve;
intercept=(path,request)=>path.endsWith('/owner-change-preview')?new Promise(done=>{resolve=()=>done(impact(request.body))}):undefined;
const read=nodes.get('owner-change-form').emit('submit');await tick();
window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));resolve();await read;
assert.equal(nodes.get('owner-change-preview-body').children.length,0);assert.equal(nodes.get('owner-change-reason').value,'');
intercept=null;await fill();await nodes.get('owner-change-form').emit('submit');
intercept=(path,request)=>path===base&&request.method==='POST'?new Promise(done=>{resolve=()=>done(detail(request.body.proposal_id))}):undefined;
const write=nodes.get('owner-change-create').emit('click');await tick();
window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'expired'}}));resolve();await write;
assert.equal(nodes.get('owner-change-detail-body').children.length,0);assert.equal(nodes.get('owner-change-retry').hidden,true);
assert.equal(nodes.get('owner-change-preview').disabled,true);
''')


def test_policy_disabled_history_pagination_and_unknown_effect_scope_are_honest_and_bilingual():
    owner_console(r'''
const first=detail('one',[]),second=detail('two',[]);
first.proposal.preview.reason='<img src=x onerror=alert(1)>';
first.proposal.preview.effect_coverage={...coverage,status:'UNKNOWN',reason:'page_limit'};
first.proposal.preview.unresolved_effects=[{effect_id:'remote:unknown',state:'DISPATCHING'},{effect_id:'remote:ready',state:'READY'}];
saved.set('one',first);saved.set('two',second);page=[first];nextCursor='owner-migration-proposal:one@r1';
options.policy='disabled';options.can_propose=false;options.blocked_reason='OWNER_CHANGE_POLICY_DISABLED';options.eligible_owners=[];
await window.OrgRebaseOwnerChange.refresh();assert.equal(nodes.get('owner-change-preview').disabled,true);await open('one');
assert(text(nodes.get('owner-change-detail-body')).includes('<img src=x onerror=alert(1)>'));
assert(text(nodes.get('owner-change-detail-body')).includes('Coverage is incomplete'));
assert(text(nodes.get('owner-change-detail-body')).includes('Claimed or unknown; reconcile'));
assert(text(nodes.get('owner-change-detail-body')).includes('Not sent; review'));
page=[second];nextCursor=null;await nodes.get('owner-change-more').emit('click');
assert(calls.some(call=>call.path.includes('&after=owner-migration-proposal%3Aone%40r1')));
assert.equal(nodes.get('owner-change-list').children.length,2);
document.documentElement.lang='zh-CN';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert.equal(nodes.get('owner-change-title').textContent,'职责移交');
assert(text(nodes.get('owner-change-detail-body')).includes('范围尚未完整核实'));
assert.equal(writes().length,0);
''')


def test_owner_workbench_is_loaded_after_the_shared_authenticated_client():
    html = (CONSOLE / "index.html").read_text()
    assert html.index("/assets/workspace-client.js") < html.index("/assets/owner-change.js")
    source = (CONSOLE / "owner-change.js").read_text()
    assert '"X-OrgRebase-Actor"' not in source
    assert "localStorage" not in source


def test_definite_rejection_requires_a_missing_record_before_unlocking_new_input():
    owner_console(r'''
await fill();await nodes.get('owner-change-form').emit('submit');
intercept=async(path,request)=>{if(path===base&&request.method==='POST')throw Object.assign(new Error('OWNER_CHANGE_SNAPSHOT_CHANGED'),{status:409,code:'OWNER_CHANGE_SNAPSHOT_CHANGED'});};
await nodes.get('owner-change-create').emit('click');assert.equal(writes().length,1);
await nodes.get('owner-change-restart').emit('click');assert.equal(nodes.get('owner-change-reason').disabled,true);
options.snapshot_digest='sha256:new-snapshot';await window.OrgRebaseOwnerChange.refresh();
assert.equal(nodes.get('owner-change-restart').hidden,false);await nodes.get('owner-change-restart').emit('click');
assert.equal(nodes.get('owner-change-reason').disabled,false);assert.equal(nodes.get('owner-change-create').disabled,true);
intercept=null;await nodes.get('owner-change-form').emit('submit');await nodes.get('owner-change-create').emit('click');
assert.equal(writes().length,2);assert.equal(writes()[1].request.body.expected_snapshot_digest,'sha256:new-snapshot');
assert.notEqual(writes()[0].request.body.proposal_id,writes()[1].request.body.proposal_id);
''')


def test_approver_options_denial_does_not_claim_the_valid_participant_was_revoked():
    owner_console(r'''
options.can_propose=false;options.blocked_reason='AUTH_ACTION_DENIED';options.eligible_owners=[];
saved.set('handover',detail());await window.OrgRebaseOwnerChange.refresh();await open();
assert(nodes.get('owner-change-policy').textContent.includes('can review transfers and perform its own confirmations'));
assert(!nodes.get('owner-change-policy').textContent.includes('access is unavailable'));
assert.equal(button('Authorize transfer of my responsibility').disabled,true);await acknowledge();
assert.equal(button('Authorize transfer of my responsibility').disabled,false);
saved.get('handover').status='REPLAN_REQUIRED';saved.get('handover').allowed_actions=[];saved.get('handover').blocked_reason='AUTH_WORKSPACE_DENIED';
await window.OrgRebaseOwnerChange.refresh();assert(text(nodes.get('owner-change-detail-body')).includes('access is unavailable'));
''')
