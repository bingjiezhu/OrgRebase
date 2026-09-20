from __future__ import annotations

import json
from pathlib import Path

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


def test_mapping_ui_clears_closed_panel_drafts_and_acknowledgment_when_identity_changes():
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(__HARNESS__);
let calls=0;
let session={mode:'oidc',authenticated:true,authentication_required:true,
 principal:{actor_id:'owner:a',tenant_id:'tenant:a',issuer:'issuer',subject:'a'}};
const value={schema_version:'orgrebase.source-binding-view.v1',record_ids:['private-record-a'],
 inventory_digest:'sha256:i',generation_digest:'sha256:g',status:'CONFIRMATION_REQUIRED',allowed_actions:['PROPOSE','CONFIRM'],gaps:[],
 inventory:{fields:[{field:'launchdate',label:'Private field A',transforms:['date_only']}]},
 candidates:[{slot_id:'launch_date',owner_id:'owner:a',owner_available:true,fields:[]}],
 proposal:{proposal_digest:'sha256:p',mappings:[{slot_id:'launch_date',record_id:'private-record-a',field:'launchdate',transform:'date_only',owner_id:'owner:a',value_map:{}}],decisions:[]}};
window.OrgRebaseClient={session:()=>session,async json(){calls++;return structuredClone(value)}};
vm.runInNewContext(__SOURCE__,{window,document,CustomEvent});
const contents=node=>[node.textContent||'',...node.children.map(contents)].join(' ');
(async()=>{
 await window.OrgRebaseSourceBinding.refresh();
 const use=nodes.get('source-binding-use-launch_date');use.checked=true;await use.emit('change');
 const field=nodes.get('source-binding-field-launch_date');field.value='launchdate';await field.emit('change');
 const ack=nodes.get('source-binding-ack');ack.checked=true;await ack.emit('change');
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:{...session,csrf_token:'renewed'}}));
 assert.equal(nodes.get('source-binding-use-launch_date').checked,true,'same identity keeps the draft');
 assert.equal(button('Confirm my field mappings').disabled,false);
 session={...session,principal:{...session.principal,actor_id:'owner:b',tenant_id:'tenant:b',subject:'b'}};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 assert.equal(nodes.get('source-binding-fields').children.length,0);
 assert(!contents(nodes.get('source-binding-review')).includes('private-record-a'));
 assert.equal(nodes.get('source-binding-propose').disabled,true);
 window.dispatchEvent(new CustomEvent('orgrebase:workspacechange'));
 assert.equal(calls,1,'closed panel waits for an explicit open');
 value.record_ids=['private-record-b'];value.proposal.mappings[0].record_id='private-record-b';
 nodes.get('source-binding-panel').open=true;await nodes.get('source-binding-panel').emit('toggle');await tick();
 assert.equal(calls,2);assert(contents(nodes.get('source-binding-review')).includes('private-record-b'));
 assert.equal(nodes.get('source-binding-use-launch_date').checked,false,'draft cleared even when generation digest matches');
 assert.equal(button('Confirm my field mappings').disabled,true,'new identity must acknowledge the mapping');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('__SOURCE__', json.dumps((ROOT / 'demo/console/source-binding.js').read_text()))
       .replace('__HARNESS__', json.dumps(str(ROOT / 'tests/workspace/console_dom_harness.js'))))


def test_mapping_ui_ignores_old_get_and_command_completion_after_identity_change():
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(__HARNESS__);
let session={mode:'oidc',authenticated:true,authentication_required:true,principal:{actor_id:'owner:a',tenant_id:'tenant:a'}};
let pending=null,hold=false,posts=0;
const value={schema_version:'orgrebase.source-binding-view.v1',record_ids:[],inventory:null,candidates:[],
 generation_digest:'sha256:g',status:'CONFIRMATION_REQUIRED',allowed_actions:['CONFIRM'],gaps:[],
 proposal:{proposal_digest:'sha256:new',mappings:[],decisions:[]}};
window.OrgRebaseClient={session:()=>session,async json(path,options={}){
 if(options.method==='POST')posts++;
 return hold?new Promise(resolve=>{pending=resolve}):structuredClone(value);
}};
vm.runInNewContext(__SOURCE__,{window,document,CustomEvent});
(async()=>{
 await window.OrgRebaseSourceBinding.refresh();nodes.get('source-binding-panel').open=true;
 hold=true;const oldRead=window.OrgRebaseSourceBinding.refresh();await tick();const resolveRead=pending;
 hold=false;session={...session,principal:{actor_id:'owner:b',tenant_id:'tenant:b'}};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));await tick();
 let ack=nodes.get('source-binding-ack');ack.checked=true;await ack.emit('change');
 const old={...value,status:'DISCOVERY_REQUIRED',allowed_actions:[],proposal:null};
 resolveRead(old);await oldRead;
 assert.equal(button('Confirm my field mappings').disabled,false,'old GET cannot erase new identity state');
 hold=true;const oldCommand=button('Confirm my field mappings').emit('click');await tick();const resolveCommand=pending;
 hold=false;session={...session,principal:{actor_id:'owner:c',tenant_id:'tenant:c'}};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));await tick();
 assert.equal(nodes.get('source-binding-workbench').getAttribute('aria-busy'),'false');
 resolveCommand(old);await oldCommand;
 assert.equal(button('Confirm my field mappings').disabled,true,'old command cannot restore acknowledgment or replace the new view');
 assert.equal(nodes.get('source-binding-notice').hidden,true,'old command cannot show a success notice');
 assert.equal(posts,1,'no business operation is replayed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('__SOURCE__', json.dumps((ROOT / 'demo/console/source-binding.js').read_text()))
       .replace('__HARNESS__', json.dumps(str(ROOT / 'tests/workspace/console_dom_harness.js'))))


def test_mapping_ui_requires_exact_owner_confirmation_and_never_replays_a_lost_response():
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(__HARNESS__);
const calls=[];let loseResponse=true;
const value={schema_version:'orgrebase.source-binding-view.v1',connector_id:'source:test',
 record_ids:['12345678-1234-1234-1234-123456789abc'],inventory_digest:'sha256:inventory',generation_digest:'sha256:generation',
 inventory:{fields:[{field:'launchdate',label:'Launch date',transforms:['date_only']}]},
 candidates:[{slot_id:'launch_date',owner_id:'owner:product',owner_available:true,fields:[{field:'launchdate'}]}],
 proposal:null,status:'MAPPING_REQUIRED',allowed_actions:['PROPOSE'],gaps:[]};
window.OrgRebaseClient={async json(path,options={}){
 calls.push({path,options:structuredClone(options)});
 if(path.endsWith('/proposals')){
  assert.equal(options.body.inventory_digest,'sha256:inventory');assert.equal(options.body.generation_digest,'sha256:generation');
  value.proposal={proposal_digest:'sha256:approved-mapping',mappings:options.body.mappings.map(item=>({...item,owner_id:'owner:product'})),decisions:[{owner_id:'owner:product',status:'PENDING'}]};
  value.status='CONFIRMATION_REQUIRED';value.allowed_actions=['CONFIRM'];
  if(loseResponse){loseResponse=false;throw new Error('RESPONSE_LOST');}
 }
 if(path.endsWith('/confirm')){
  assert.equal(path,'/api/workspace/source-binding/proposals/approved-mapping/confirm');
  assert.deepEqual(Object.keys(options.body),['proposal_digest']);assert.equal(options.body.proposal_digest,'sha256:approved-mapping');
  value.proposal.decisions[0].status='CONFIRMED';value.allowed_actions=['REVOKE'];value.status='CONFIRMED';
 }
 return structuredClone(value);
}};
vm.runInNewContext(__SOURCE__,{window,document,CustomEvent});
(async()=>{
 await tick();assert.equal(calls.length,0,'closed panel does not load history');
 nodes.get('source-binding-panel').open=true;await nodes.get('source-binding-panel').emit('toggle');await tick();
 assert.equal(nodes.get('source-binding-propose').disabled,true,'name matches are not accepted mappings');
 const use=nodes.get('source-binding-use-launch_date');use.checked=true;await use.emit('change');
 const field=nodes.get('source-binding-field-launch_date');field.value='launchdate';await field.emit('change');
 assert.equal(nodes.get('source-binding-propose').disabled,false);
 await nodes.get('source-binding-form').emit('submit');await tick();
 assert.equal(calls.filter(call=>call.options.method==='POST').length,1);
 assert.equal(nodes.get('source-binding-propose').disabled,true,'uncertain result requires refresh');
 await window.OrgRebaseSourceBinding.refresh();await tick();
 assert.equal(calls.filter(call=>call.options.method==='POST').length,1,'refresh never replays the proposal');
 assert.equal(button('Confirm my field mappings').disabled,true);
 const ack=nodes.get('source-binding-ack');ack.checked=true;await ack.emit('change');
 assert.equal(button('Confirm my field mappings').disabled,false);
 await button('Confirm my field mappings').emit('click');await tick();
 assert.equal(value.status,'CONFIRMED');
 assert.equal(calls.filter(call=>call.options.method==='POST').length,2);
 assert(!calls.some(call=>/sync|apply/.test(call.path)),'mapping confirmation does not execute business changes');
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));
 assert.equal(nodes.get('source-binding-fields').children.length,0);
 assert.equal(nodes.get('source-binding-propose').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('__SOURCE__', json.dumps((ROOT / 'demo/console/source-binding.js').read_text()))
       .replace('__HARNESS__', json.dumps(str(ROOT / 'tests/workspace/console_dom_harness.js'))))


def test_mapping_ui_discards_late_identity_response_and_refuses_stale_mapping_acknowledgment():
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(__HARNESS__);
let resolve,delayed=false,posts=0;
const value={schema_version:'orgrebase.source-binding-view.v1',record_ids:[],inventory:null,candidates:[],
 generation_digest:'sha256:g1',status:'CONFIRMATION_REQUIRED',allowed_actions:['CONFIRM'],gaps:[],
 proposal:{proposal_digest:'sha256:p1',mappings:[],decisions:[]}};
window.OrgRebaseClient={async json(path,options={}){if(options.method==='POST')posts++;return delayed?new Promise(done=>{resolve=done}):structuredClone(value)}};
vm.runInNewContext(__SOURCE__,{window,document,CustomEvent});
(async()=>{
 await window.OrgRebaseSourceBinding.refresh();
 let ack=nodes.get('source-binding-ack');ack.checked=true;await ack.emit('change');
 assert.equal(button('Confirm my field mappings').disabled,false);
 value.proposal.proposal_digest='sha256:p2';await window.OrgRebaseSourceBinding.refresh();
 assert.equal(button('Confirm my field mappings').disabled,true,'another proposal needs new acknowledgment');
 delayed=true;const pending=window.OrgRebaseSourceBinding.refresh();await tick();
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'expired'}}));
 resolve(structuredClone(value));await pending;
 assert.equal(nodes.get('source-binding-review').children.length,1,'late private data stays cleared');
 assert.equal(nodes.get('source-binding-propose').disabled,true);assert.equal(posts,0);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('__SOURCE__', json.dumps((ROOT / 'demo/console/source-binding.js').read_text()))
       .replace('__HARNESS__', json.dumps(str(ROOT / 'tests/workspace/console_dom_harness.js'))))


def test_mapping_ui_binds_selected_record_and_processed_values_and_discards_changed_generation():
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick,button}=require(__HARNESS__);
const calls=[];
const value={schema_version:'orgrebase.source-binding-view.v1',record_ids:['record-a','record-b'],
 inventory_digest:'sha256:i1',generation_digest:'sha256:g1',status:'MAPPING_REQUIRED',allowed_actions:['PROPOSE'],gaps:[],proposal:null,
 inventory:{fields:[{field:'launchdate',label:'Launch date',transforms:['date_only']}]},
 candidates:[{slot_id:'launch_date',owner_id:'owner:product',owner_available:true,fields:[]}]};
window.OrgRebaseClient={async json(path,options={}){calls.push({path,options:structuredClone(options)});return structuredClone(value)}};
vm.runInNewContext(__SOURCE__,{window,document,CustomEvent});
(async()=>{
 await window.OrgRebaseSourceBinding.refresh();
 const use=nodes.get('source-binding-use-launch_date');use.checked=true;await use.emit('change');
 const field=nodes.get('source-binding-field-launch_date');field.value='launchdate';await field.emit('change');
 assert.equal(nodes.get('source-binding-propose').disabled,true,'multiple records require an explicit choice');
 const record=nodes.get('source-binding-record-launch_date');record.value='record-b';await record.emit('change');
 for(let i=0;i<2;i++){
  await button('Add value mapping').emit('click');
  assert.equal(document.activeElement,nodes.get(`source-binding-value-launch_date-${i}-from`),'keyboard focus reaches the new mapping');
  for(const [key,text] of [['from','2026-10-10'],['to','2026-10-11']]){const input=nodes.get(`source-binding-value-launch_date-${i}-${key}`);input.value=text;await input.emit('input');}
 }
 await nodes.get('source-binding-form').emit('submit');assert.equal(calls.filter(call=>call.options.method==='POST').length,0,'duplicate source values cannot be submitted');
 const input=nodes.get('source-binding-value-launch_date-1-from');input.value='2026-10-12';await input.emit('input');
 await nodes.get('source-binding-form').emit('submit');
 const body=calls.find(call=>call.options.method==='POST').options.body;
 assert.deepEqual(JSON.parse(JSON.stringify(body.mappings)),[{slot_id:'launch_date',record_id:'record-b',field:'launchdate',transform:'date_only',value_map:{'2026-10-10':'2026-10-11','2026-10-12':'2026-10-11'}}]);
 value.generation_digest='sha256:g2';await window.OrgRebaseSourceBinding.refresh();
 assert.equal(nodes.get('source-binding-use-launch_date').checked,false);
 assert.equal(nodes.get('source-binding-record-launch_date').value,'');
 assert.equal(nodes.get('source-binding-propose').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('__SOURCE__', json.dumps((ROOT / 'demo/console/source-binding.js').read_text()))
       .replace('__HARNESS__', json.dumps(str(ROOT / 'tests/workspace/console_dom_harness.js'))))
