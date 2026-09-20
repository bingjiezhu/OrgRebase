from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require('./tests/workspace/console_dom_harness.js');
const calls=[],items=[];
let uuid=0,finishPost=null,delayPost=false;
const configuration={execution_run_id:'run:one',fields:['product_plan','currency'].map(slot=>({
 slot_id:slot,label:slot==='product_plan'?'产品方案':'报价币种',value_kind:'text',
 current:{version:'v1',digest:'base:'+slot,value:'Original',state:'CURRENT'},
 owner_id:'owner:product',allowed_operations:['UPDATE'],
}))};
const sessionIdentity={mode:'local'};
window.OrgRebaseClient={session:()=>sessionIdentity,async json(path,request={}){
 calls.push({path,request:structuredClone(request)});
 if(path.endsWith('/change-options'))return structuredClone(configuration);
 if(path.includes('/changes?'))return {items:structuredClone(items),next_cursor:null};
 if(path.endsWith('/change-proposals')){
  const body=request.body;
  items.push({execution_run_id:'run:one',event:{event_id:body.event_id,digest:'event:'+body.event_id,
    slot_id:body.slot_id,owner_id:'owner:product',base_version:body.base_version,base_digest:body.base_digest,
    proposal:{payload:{canonical_value:body.value},source_refs:[body.source_ref]}},status:'RECEIVED',allowed_actions:[]});
  if(delayPost)return new Promise(resolve=>{finishPost=()=>resolve({event_id:body.event_id});});
  return {event_id:body.event_id};
 }
 return structuredClone(items.find(item=>item.event.event_id===decodeURIComponent(path.split('/').at(-1))));
}};
vm.runInNewContext(fs.readFileSync('demo/console/change-workbench.js','utf8'),
 {window,document,CustomEvent,performance,crypto:{randomUUID:()=>String(++uuid)}});
async function edit(value,source='source:one'){
 nodes.get('change-value').value=value;await nodes.get('change-value').emit('input');
 nodes.get('change-source').value=source;await nodes.get('change-source').emit('input');
}
const submissions=()=>calls.filter(call=>call.path.endsWith('/change-proposals'));
'''

SCENARIOS = {
    "currency_requiring_joint_price_change_is_explained_and_cannot_submit": r'''
configuration.fields[1].allowed_operations=[];
configuration.fields[1].blocked_reason='PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE';
await window.OrgRebaseChangeWorkbench.refresh();
nodes.get('change-field').value='currency';await nodes.get('change-field').emit('change');
assert.equal(nodes.get('change-proposal-form').hidden,true);
assert.equal(nodes.get('change-submit').disabled,true);
assert(nodes.get('change-edit-blocked').textContent.includes('cannot be changed independently'));
assert(!nodes.get('change-edit-blocked').textContent.includes('identity'));
await nodes.get('change-proposal-form').emit('submit');
assert.equal(submissions().length,0);
document.documentElement.lang='zh-CN';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert(nodes.get('change-edit-blocked').textContent.includes('暂不支持单独修改'));
nodes.get('change-field').value='product_plan';await nodes.get('change-field').emit('change');
assert.equal(nodes.get('change-proposal-form').hidden,false);
assert.equal(nodes.get('change-submit').disabled,false);
''',
    "pending_submission_preserves_new_edits_and_blocks_duplicate_clicks": r'''
await edit('Submitted A');nodes.get('change-editor').open=true;delayPost=true;
const pending=nodes.get('change-proposal-form').emit('submit');await tick();
await edit('Unsent B','source:two');
await nodes.get('change-proposal-form').emit('submit');
assert.equal(submissions().length,1,'a pending request cannot be submitted twice');
finishPost();await pending;
assert.equal(items[0].event.proposal.payload.canonical_value,'Submitted A');
assert.equal(nodes.get('change-value').value,'Unsent B','a success must not clear subsequent edits');
assert.equal(nodes.get('change-source').value,'source:two');
assert.equal(nodes.get('change-editor').open,true);
delayPost=false;await nodes.get('change-proposal-form').emit('submit');
assert.equal(submissions().length,2);
assert.notEqual(submissions()[0].request.body.event_id,submissions()[1].request.body.event_id);
assert.equal(items[1].event.proposal.payload.canonical_value,'Unsent B');
assert.equal(nodes.get('change-value').value,'','an unchanged submitted draft still clears normally');
assert.equal(nodes.get('change-editor').open,false);
assert.equal(calls.filter(call=>call.request.method==='POST').length,2,'registration never approves or applies');
''',
    "changing_stale_field_restores_submit_for_new_current_base": r'''
await edit('Old draft');
configuration.fields[0].current.digest='updated-product-base';
await window.OrgRebaseChangeWorkbench.refresh();
assert.equal(nodes.get('change-submit').disabled,true);
nodes.get('change-field').value='currency';await nodes.get('change-field').emit('change');
assert.equal(nodes.get('change-submit').dataset.unavailable,'false');
assert.equal(nodes.get('change-submit').disabled,false,'a valid new field must not inherit the stale disabled state');
await edit('USD');await nodes.get('change-proposal-form').emit('submit');
assert.equal(submissions().length,1);
assert.equal(submissions()[0].request.body.slot_id,'currency');
assert.equal(submissions()[0].request.body.base_digest,'base:currency');
''',
    "language_change_updates_field_options_and_retains_draft": r'''
nodes.get('change-field').value='currency';await nodes.get('change-field').emit('change');
await edit('USD','source:currency');
document.documentElement.lang='zh-CN';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert.deepEqual(nodes.get('change-field').children.map(option=>option.textContent),['产品方案','报价币种']);
assert.equal(nodes.get('change-field').value,'currency');assert.equal(nodes.get('change-value').value,'USD');
assert.equal(nodes.get('change-source').value,'source:currency');
document.documentElement.lang='en';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert.deepEqual(nodes.get('change-field').children.map(option=>option.textContent),['Product plan','Currency']);
assert.equal(nodes.get('change-value').value,'USD');assert.equal(submissions().length,0);
''',
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_change_workbench_wrapup(scenario):
    run_node(HARNESS + "\n(async()=>{await tick();await tick();\n" + SCENARIOS[scenario]
             + "\n})().catch(error=>{console.error(error);process.exitCode=1;});")
