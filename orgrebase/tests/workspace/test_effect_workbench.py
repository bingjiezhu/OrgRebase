from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_effect_workbench_keeps_approval_dispatch_and_unknown_reconciliation_separate() -> None:
    source = json.dumps((ROOT / "demo/console/effect-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict');const vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);
const calls=[],items=[];let uuid=0,loseResponse=true;
const configuration={target_key:'https://target.example/quote',action:'update',owner_id:'owner:one',can_propose:true,fields:{name:{max_length:300},description:{max_length:2000}},observations:[{observation_id:'observation:one',current:true,observed_at:'now',version:'W/"41"',fields:{name:'Before',description:'Prior note'}}],next_cursor:null};
window.OrgRebaseClient={async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path==='/api/workspace/effect-options')return structuredClone(configuration);
 if(path.startsWith('/api/workspace/effect-proposals?'))return {items:structuredClone(items),next_cursor:null};
 if(path==='/api/workspace/effect-proposals'){
  const payload=request.body;
  if(!items.length)items.push({proposal:{...payload,target_key:configuration.target_key,expected_version:'W/"41"',owner_id:'owner:one',previous_values:{name:'Before',description:'Prior note'}},proposal_digest:'sha256:proposal',state:'PENDING_APPROVAL',allowed_actions:['APPROVE','REJECT'],approval:null,effect:null});
  if(loseResponse){loseResponse=false;throw new Error('NETWORK_LOST');}
  return structuredClone(items[0]);
 }
 if(path.endsWith('/approve')){assert.deepEqual(JSON.parse(JSON.stringify(request.body)),{proposal_digest:'sha256:proposal'});items[0].state='READY';items[0].effect={request_digest:'sha256:effect',requested_action:null};items[0].allowed_actions=['EXECUTE'];return structuredClone(items[0]);}
 if(path.endsWith('/actions')){
  assert.equal(request.body.request_digest,'sha256:effect');items[0].effect.requested_action=request.body.action;items[0].allowed_actions=[];return structuredClone(items[0]);
 }
 return structuredClone(items[0]);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=>String(++uuid)}});
(async()=>{
 await tick();await tick();
 assert.equal(nodes.get('effect-form').hidden,false);
 nodes.get('effect-field-name').value='Owner reviewed title';await nodes.get('effect-field-name').emit('input');
 nodes.get('effect-field-description').value='Owner reviewed note';await nodes.get('effect-field-description').emit('input');
 nodes.get('effect-reason').value='Customer wording correction';await nodes.get('effect-reason').emit('input');
 await nodes.get('effect-form').emit('submit');
 assert.equal(nodes.get('effect-field-name').value,'Owner reviewed title');
 await nodes.get('effect-form').emit('submit');await tick();
 const proposals=calls.filter(call=>call.path==='/api/workspace/effect-proposals');
 assert.deepEqual(proposals[0].request.body,proposals[1].request.body,'response loss retains exact proposal identity and content');
 assert.equal(items.length,1);assert.equal(button('Approve external operation').disabled,true);
 assert.equal(nodes.get('effect-detail-body').children[0].children.length,7,'target, version, owner, reason and every changed field displayed');
 nodes.get('effect-approval-ack').checked=true;await nodes.get('effect-approval-ack').emit('change');
 assert.equal(document.activeElement,nodes.get('effect-approval-ack'),'keyboard acknowledgement retains focus');
 await button('Approve external operation').emit('click');await tick();
 assert.equal(items[0].state,'READY');
 assert.equal(calls.filter(call=>call.path.endsWith('/actions')).length,0,'approval never queues dispatch');
 await button('Send approved operation').emit('click');await tick();
 assert.equal(items[0].effect.requested_action,'EXECUTE');
 assert.equal(items[0].state,'READY','queue acceptance cannot be relabelled confirmed');
 assert.match(nodes.get('effect-notice').textContent,/queued does not mean successful/);
 items[0].state='COMMIT_UNKNOWN';items[0].effect.requested_action=null;items[0].allowed_actions=['EXECUTE','QUERY','CANCEL'];
 await window.OrgRebaseEffectWorkbench.refresh();
 assert.equal(nodes.get('effect-detail-title').textContent,'Unknown; reconcile or safely cancel');
 assert(!nodes.get('effect-workbench').querySelectorAll('button').some(item=>item.textContent==='Send approved operation'),'UNKNOWN never offers resend even with inconsistent advertised action');
 await button('Query target receipt').emit('click');await tick();
 assert.equal(calls.filter(call=>call.path.endsWith('/actions')).at(-1).request.body.action,'QUERY');
 assert.equal(items[0].state,'COMMIT_UNKNOWN');
 items[0].effect.requested_action=null;items[0].allowed_actions=['QUERY','CANCEL'];await window.OrgRebaseEffectWorkbench.refresh();
 await button('Request safe cancellation').emit('click');await tick();
 assert.equal(items[0].state,'COMMIT_UNKNOWN','cancel queued is not a confirmed cancellation');
 configuration.observations[0].current=false;await window.OrgRebaseEffectWorkbench.refresh();
 assert.equal(nodes.get('effect-form').hidden,true,'expired target observation cannot silently bind a new draft');
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));
 assert.equal(nodes.get('effect-list').children.length,0);assert.equal(nodes.get('effect-form').hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))


def test_effect_workbench_asset_loads_after_shell_and_shared_client() -> None:
    html = (ROOT / "demo/console/index.html").read_text()
    assert html.index('/assets/workspace-client.js') < html.index('/assets/workspace-shell.js') < html.index('/assets/effect-workbench.js')


@pytest.mark.parametrize("reason", ["logout", "role-switch", "expired"])
def test_effect_draft_retention_on_session_end(reason: str) -> None:
    source = json.dumps((ROOT / "demo/console/effect-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
let writes=0;
const configuration={target_key:'https://target.example/quote',can_propose:true,
 fields:{name:{max_length:300}},observations:[{observation_id:'current',current:true,
 observed_at:'now',version:'v1',fields:{name:'Current title'}}]};
window.OrgRebaseClient={async json(path,request={}){
 if(request.method==='POST')++writes;
 return path.endsWith('/effect-options')?structuredClone(configuration):{items:[],next_cursor:null};
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=> 'unused'}});
(async()=>{
 await tick();await tick();
 nodes.get('effect-field-name').value='Private unsent correction';await nodes.get('effect-field-name').emit('input');
 nodes.get('effect-reason').value='Private unsent reason';await nodes.get('effect-reason').emit('input');
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:REASON}}));
 assert.equal(nodes.get('effect-form').hidden,true);
 await window.OrgRebaseEffectWorkbench.refresh();
 assert.equal(nodes.get('effect-field-name').value,REASON==='expired'?'Private unsent correction':'');
 assert.equal(nodes.get('effect-reason').value,REASON==='expired'?'Private unsent reason':'');
 assert.equal(writes,0,'session recovery never sends a draft');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source).replace('REASON', json.dumps(reason)))


@pytest.mark.parametrize("changed", ["same_identity", "actor_id", "tenant_id", "issuer", "subject", "workspace"])
def test_effect_draft_reauthentication_requires_same_identity_and_workspace(changed: str) -> None:
    source = json.dumps((ROOT / "demo/console/effect-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
const original={mode:'oidc',authentication_required:true,authenticated:true,csrf_token:'old-token',
 principal:{issuer:'https://issuer.example',subject:'person:A',tenant_id:'tenant:A',actor_id:'actor:A'}};
let session=structuredClone(original),workspace='workspace:A',writes=0;
const configuration={target_key:'https://target.example/quote',can_propose:true,
 fields:{name:{max_length:300}},observations:[{observation_id:'current',current:true,
 observed_at:'now',version:'v1',fields:{name:'Current title'}}]};
window.OrgRebaseClient={session:()=>session,workspace:()=>workspace,async json(path,request={}){
 if(request.method==='POST')++writes;
 return path.endsWith('/effect-options')?structuredClone(configuration):{items:[],next_cursor:null};
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=> 'unused'}});
(async()=>{
 await tick();await tick();
 nodes.get('effect-field-name').value='Private correction';await nodes.get('effect-field-name').emit('input');
 nodes.get('effect-reason').value='Private reason';await nodes.get('effect-reason').emit('input');
 session={...session,authenticated:false,principal:null,csrf_token:null};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'expired'}}));
 assert.equal(nodes.get('effect-form').hidden,true);
 session=structuredClone(original);session.csrf_token='renewed-token';
 if(!['same_identity','workspace'].includes(CHANGED))session.principal[CHANGED]+=':new';
 if(CHANGED==='workspace')workspace='workspace:B';
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 window.dispatchEvent(new CustomEvent('orgrebase:workspacechange'));await tick();await tick();
 const same=CHANGED==='same_identity';
 assert.equal(nodes.get('effect-field-name').value,same?'Private correction':'');
 assert.equal(nodes.get('effect-reason').value,same?'Private reason':'');
 assert.equal(writes,0,'reauthentication never submits a retained draft');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('CHANGED', json.dumps(changed)).replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.parametrize("pending_operation", ["refresh", "submit", "approve"])
def test_effect_previous_identity_response_cannot_restore_state(pending_operation: str) -> None:
    source = json.dumps((ROOT / "demo/console/effect-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);
let session={mode:'oidc',authentication_required:true,authenticated:true,
 principal:{issuer:'issuer',subject:'person:A',tenant_id:'tenant',actor_id:'actor:A'}};
let defer=false,release,heldBody,writes=0;
const configuration={target_key:'https://target.example/quote',can_propose:true,
 fields:{name:{max_length:300}},observations:[{observation_id:'current',current:true,
 observed_at:'now',version:'v1',fields:{name:'Current title'}}]};
const item={proposal:{proposal_id:'proposal:A',target_key:configuration.target_key,expected_version:'v1',
 owner_id:'actor:A',reason:'A reason',changes:{name:'A name'},previous_values:{name:'Current title'}},
 proposal_digest:'sha256:proposal',state:'PENDING_APPROVAL',allowed_actions:['APPROVE'],effect:null};
window.OrgRebaseClient={session:()=>session,workspace:()=> 'same-workspace',async json(path,request={}){
 if(request.method==='POST')++writes;
 if(defer&&session.principal.actor_id==='actor:A'&&
   (OP==='refresh'&&path.endsWith('/effect-options')||OP==='submit'&&request.method==='POST'||OP==='approve'&&path.endsWith('/approve'))){
  heldBody=request.body;return new Promise(resolve=>{release=resolve;});
 }
 if(path.endsWith('/effect-options'))return structuredClone(configuration);
 if(path.includes('/effect-proposals?'))return {items:OP==='approve'&&session.principal.actor_id==='actor:A'?[structuredClone(item)]:[],next_cursor:null};
 return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=> 'proposal-A'}});
(async()=>{
 await tick();await tick();
 nodes.get('effect-field-name').value='A draft';await nodes.get('effect-field-name').emit('input');
 nodes.get('effect-reason').value='A draft reason';await nodes.get('effect-reason').emit('input');
 if(OP==='approve'){nodes.get('effect-approval-ack').checked=true;await nodes.get('effect-approval-ack').emit('change');}
 defer=true;
 const pending=OP==='refresh'?window.OrgRebaseEffectWorkbench.refresh():OP==='submit'?nodes.get('effect-form').emit('submit'):button('Approve external operation').emit('click');
 await tick();assert.equal(typeof release,'function');
 session={...session,principal:{...session.principal,actor_id:'actor:B',subject:'person:B'}};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 window.dispatchEvent(new CustomEvent('orgrebase:workspacechange'));await tick();await tick();
 assert.equal(nodes.get('effect-form').hidden,false);
 nodes.get('effect-field-name').value='B draft';await nodes.get('effect-field-name').emit('input');
 nodes.get('effect-reason').value='B reason';await nodes.get('effect-reason').emit('input');
 release(OP==='refresh'?structuredClone(configuration):{...structuredClone(item),proposal:{...item.proposal,proposal_id:heldBody?.proposal_id||item.proposal.proposal_id}});
 await pending;await tick();await tick();
 assert.equal(nodes.get('effect-field-name').value,'B draft');
 assert.equal(nodes.get('effect-reason').value,'B reason');
 assert.equal(nodes.get('effect-list').children.length,0);
 assert.equal(nodes.get('effect-detail-body').children.length,0);
 assert.equal(nodes.get('effect-notice').hidden,true);
 assert.equal(nodes.get('effect-workbench').getAttribute('aria-busy'),'false');
 assert.equal(writes,OP==='refresh'?0:1,'old responses never replay an operation');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('OP', json.dumps(pending_operation)).replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_effect_detail_refresh_preserves_focus_and_requires_reload_after_failure() -> None:
    source = json.dumps((ROOT / "demo/console/effect-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict');const vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);
const configuration={target_key:'https://target.example/quote',can_propose:true,fields:{name:{max_length:300}},observations:[{observation_id:'observation:one',current:true,observed_at:'now',version:'v1',fields:{name:'Before'}}]};
const item={proposal:{proposal_id:'external:one',target_key:configuration.target_key,expected_version:'v1',owner_id:'owner:one',reason:'Correct wording',changes:{name:'After'},previous_values:{name:'Before'}},proposal_digest:'sha256:one',state:'COMMIT_UNKNOWN',allowed_actions:['QUERY','CANCEL'],effect:{request_digest:'sha256:effect'}};
let failDetail=false,loseCommand=false;const commands=[];
window.OrgRebaseClient={async json(path,request={}){
 if(path.endsWith('/effect-options'))return structuredClone(configuration);
 if(path.includes('/effect-proposals?'))return {items:[structuredClone(item)],next_cursor:null};
 if(path.endsWith('/actions')){commands.push(request.body.action);if(loseCommand)throw new Error('NETWORK_LOST');return structuredClone(item);}
 if(failDetail)throw new Error('DETAIL_UNAVAILABLE');
 return structuredClone(item);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=> 'unused'}});
(async()=>{
 await tick();await tick();
 const outside=document.createElement('button');outside.focus();
 await window.OrgRebaseEffectWorkbench.refresh();
 assert.equal(document.activeElement,outside,'background refresh must not steal focus from other work');
 const list=nodes.get('effect-list').children[0].children[0];list.focus();await list.emit('click');
 assert.equal(document.activeElement,nodes.get('effect-detail-title'),'explicit selection opens the detail for keyboard users');
 nodes.get('effect-audit').open=true;nodes.get('effect-audit-label').focus();
 await window.OrgRebaseEffectWorkbench.refresh();
 assert.equal(nodes.get('effect-audit').open,true,'refresh keeps expanded receipt evidence readable');
 assert.equal(document.activeElement,nodes.get('effect-audit-label'));
 failDetail=true;await window.OrgRebaseEffectWorkbench.refresh();
 assert.equal(nodes.get('effect-detail-unavailable').hidden,false);
 assert.equal(nodes.get('effect-detail-title').textContent,'Unknown; reconcile or safely cancel','a failed read cannot claim completion');
 assert.equal(button('Query target receipt').disabled,true);assert.equal(button('Request safe cancellation').disabled,true);
 await button('Query target receipt').emit('click');assert.equal(commands.length,0,'cached actions cannot bypass the reload requirement');
 assert.equal(nodes.get('effect-refresh').disabled,false,'read-only recovery remains reachable');
 failDetail=false;await nodes.get('effect-refresh').emit('click');
 assert.equal(nodes.get('effect-detail-unavailable').hidden,true);
 assert.equal(nodes.get('effect-notice').hidden,true,'a recovered read clears the obsolete failure notice');
 assert.equal(button('Query target receipt').disabled,false);assert.equal(button('Request safe cancellation').disabled,false);
 loseCommand=true;await button('Query target receipt').emit('click');
 assert.equal(commands.length,1);assert.equal(button('Query target receipt').disabled,true,'a lost command response requires explicit reconciliation');
 await button('Query target receipt').emit('click');assert.equal(commands.length,1,'no automatic or repeated dispatch from stale detail');
 loseCommand=false;await window.OrgRebaseEffectWorkbench.refresh();await button('Request safe cancellation').emit('click');
 assert.deepEqual(commands,['QUERY','CANCEL']);
 assert.equal(item.state,'COMMIT_UNKNOWN','queue acceptance never confirms cancellation');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_effect_revision_uses_current_observation_and_new_identity_without_prior_authority() -> None:
    source = json.dumps((ROOT / "demo/console/effect-workbench.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict');const vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(HARNESS);
const configuration={target_key:'https://target.example/quote',can_propose:true,fields:{name:{max_length:300}},observations:[{observation_id:'observation:current',current:true,observed_at:'now',version:'v2',fields:{name:'Current title'}}]};
const original={proposal:{proposal_id:'external:rejected',observation_id:'observation:old',target_key:configuration.target_key,expected_version:'v1',owner_id:'owner:one',reason:'Correct customer wording',changes:{name:'Suggested title',description:'Field no longer editable'},previous_values:{name:'Old title',description:'Old note'}},proposal_digest:'sha256:old',state:'REJECTED',allowed_actions:[],approval:{approval_digest:'sha256:old-approval'},effect:{request_digest:'sha256:old-effect'}};
const items=[original],calls=[];let uuid=0;
window.OrgRebaseClient={async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path.endsWith('/effect-options'))return structuredClone(configuration);
 if(path.includes('/effect-proposals?'))return {items:structuredClone(items),next_cursor:null};
 if(path.endsWith('/effect-proposals')){
  const created={proposal:{...request.body,target_key:configuration.target_key,expected_version:'v2',owner_id:'owner:one',previous_values:{name:'Current title'}},proposal_digest:'sha256:new',state:'PENDING_APPROVAL',allowed_actions:['APPROVE','REJECT'],approval:null,effect:null};items.push(created);return structuredClone(created);
 }
 return structuredClone(items.find(item=>path.endsWith(encodeURIComponent(item.proposal.proposal_id))));
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,crypto:{randomUUID:()=>String(++uuid)}});
(async()=>{
 await tick();await tick();await button('Revise as a new proposal').emit('click');
 assert.equal(nodes.get('effect-editor').open,true);
 assert.equal(nodes.get('effect-observation').value,'observation:current');
 assert.equal(nodes.get('effect-field-name').value,'Suggested title');
 assert.equal(document.activeElement,nodes.get('effect-field-name'));
 assert.equal(calls.filter(call=>call.request.method==='POST').length,0,'revision only prepares an editable candidate');
 await nodes.get('effect-form').emit('submit');
 const submitted=calls.find(call=>call.request.method==='POST');
 assert.deepEqual(JSON.parse(JSON.stringify(submitted.request.body)),{proposal_id:'external:1',observation_id:'observation:current',changes:{name:'Suggested title'},reason:'Correct customer wording'});
 assert.equal(original.state,'REJECTED');assert.equal(original.approval.approval_digest,'sha256:old-approval');
 assert.equal(items[1].state,'PENDING_APPROVAL');assert.equal(items[1].approval,null);assert.equal(items[1].effect,null);
 assert.equal(button('Approve external operation').disabled,true,'new proposal requires a new field acknowledgement');
 items[1].state='COMMIT_UNKNOWN';items[1].effect={request_digest:'sha256:new-effect'};items[1].allowed_actions=['QUERY','CANCEL'];
 await window.OrgRebaseEffectWorkbench.refresh();
 assert(!nodes.get('effect-workbench').querySelectorAll('button').some(item=>item.textContent==='Revise as a new proposal'),'UNKNOWN cannot create a replacement proposal from the recovery detail');
 items[1].state='PENDING_APPROVAL';items[1].effect=null;items[1].allowed_actions=[];items[1].blocked_reason='EFFECT_TARGET_VERSION_STALE';
 await window.OrgRebaseEffectWorkbench.refresh();assert.equal(button('Revise as a new proposal').disabled,false,'an invalid unapproved proposal can be revised');
 configuration.can_propose=false;await window.OrgRebaseEffectWorkbench.refresh();
 assert(!nodes.get('effect-workbench').querySelectorAll('button').some(item=>item.textContent==='Revise as a new proposal'),'revision respects current proposal authority');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))
