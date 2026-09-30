from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "demo" / "console" / "deliverable-set.js"


def test_deliverable_set_console_is_independent_and_uses_current_principal_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "/api/workspace/deliverable-set/changes/" in source
    assert "/api/workspace/approve/" in source
    assert "changeWorkbench.after(panel)" in source
    assert "operation_id" in source and "preview_digest" in source
    assert "actor_id:" not in source and "owner_id:" not in source
    assert "raw_contract_text" not in source and "internal_cost_floor" not in source


def test_deliverable_set_console_has_valid_javascript() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for console syntax validation")
    subprocess.run([node, "--check", str(SCRIPT)], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_deliverable_set_console_fences_old_selection_and_clears_session_content() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
class Element {
  constructor(tag,id=null){this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.hidden=false;this.textContent='';this.disabled=false;this.listeners={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  addEventListener(type,fn){this.listeners[type]=fn;}
  querySelectorAll(tag){const out=[];const visit=n=>{if(n.tagName===tag.toUpperCase())out.push(n);for(const c of n.children||[])visit(c);};visit(this);return out;}
  set innerHTML(value){for(const match of value.matchAll(/<([a-z0-9-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g))nodes[match[2]]=new Element(match[1],match[2]);this.children=Object.values(nodes).filter(n=>n.id?.startsWith('deliverable-set-'));}
}
const nodes={'task-changes-flow':new Element('div','task-changes-flow')};
let selected=null;const listeners={},calls=[];
const pending=(path,options)=>new Promise((resolve,reject)=>calls.push({path,options,resolve,reject}));
const document={documentElement:{lang:'en'},getElementById:id=>nodes[id]||null,createElement:tag=>new Element(tag),querySelector:selector=>selector==='#change-audit pre'&&selected?{textContent:JSON.stringify({event_id:selected})}:null};
const window={OrgRebaseClient:{json:pending,session:()=>({actors:[]})},addEventListener:(type,fn)=>{(listeners[type]??=[]).push(fn);},dispatchEvent:event=>{for(const fn of listeners[event.type]||[])fn(event);}};
const context=vm.createContext({window,document,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},setTimeout:fn=>fn(),Map,Set,encodeURIComponent});
vm.runInContext(fs.readFileSync('demo/console/deliverable-set.js','utf8'),context);
const tick=()=>new Promise(setImmediate),value=(id,actions=[])=>({preview_digest:'sha256:'+id.repeat(64).slice(0,64),candidate_set:{digest:'sha256:'+id.repeat(64).slice(0,64),members:[{deliverable_kind:'QUOTE',disposition:'REBUILD',owner_id:'owner',candidate_ref:`quote@${id}`}]},decisions:[],pending_owner_ids:['owner'],current_principal_owner_id:'owner',allowed_actions:actions,approval_set:null});
(async()=>{
  assert.equal(calls[0].path,'/api/workspace/deliverable-set');calls[0].resolve({members:[]});await tick();
  selected='a';window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));const a=calls.at(-1);assert(a.path.endsWith('/a'));
  selected='b';window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));const b=calls.at(-1);assert(b.path.endsWith('/b'));
  b.resolve(value('b'));await tick();assert(JSON.stringify(nodes['deliverable-set-members']).includes('quote@b'));
  a.resolve(value('a'));await tick();assert(!JSON.stringify(nodes['deliverable-set-members']).includes('quote@a'),'late A cannot overwrite B');
  window.dispatchEvent(new CustomEvent('orgrebase:sessionended'));assert.equal(nodes['deliverable-set-members'].children.length,0);assert.equal(nodes['deliverable-set-notice'].textContent,'');
  selected='c';window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));const c=calls.at(-1);c.resolve(value('c',['APPROVE']));await tick();
  const action=nodes['deliverable-set-members'].querySelectorAll('button')[0];assert(action);action.listeners.click();action.listeners.click();assert.equal(calls.filter(call=>call.path.includes('/approve/')).length,1,'busy command is single-flight');
  calls.at(-1).resolve({});await tick();
  calls.at(-1).resolve(value('c'));await tick();
  assert.equal(nodes['deliverable-set-refresh'].disabled,false,'successful workspace refresh cannot strand the refresh button');
  calls.at(-1).resolve({members:[]});await tick();
  calls.at(-1).resolve(value('c',['APPROVE']));await tick();
  const secondAction=nodes['deliverable-set-members'].querySelectorAll('button')[0];secondAction.listeners.click();
  const oldCommand=calls.at(-1);
  window.dispatchEvent(new CustomEvent('orgrebase:sessionended'));
  oldCommand.reject(new Error('STALE_ACTOR_ERROR_SENTINEL'));await tick();
  assert.equal(nodes['deliverable-set-notice'].textContent,'','old command error cannot appear in another session');
  assert.equal(nodes['deliverable-set-refresh'].disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_current_memo_is_visible_and_read_failures_clear_content_without_hiding_retry() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
class Element {
 constructor(tag,id=null){this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.hidden=false;this.textContent='';this.disabled=false;this.listeners={};}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[...items];}
 addEventListener(type,fn){this.listeners[type]=fn;}async emit(type){await this.listeners[type]?.();}
 querySelectorAll(tag){const out=[];const visit=n=>{if(n.tagName===tag.toUpperCase())out.push(n);for(const c of n.children||[])visit(c);};visit(this);return out;}
 set innerHTML(value){for(const m of value.matchAll(/<([a-z0-9-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g))nodes[m[2]]=new Element(m[1],m[2]);this.children=Object.values(nodes).filter(n=>n.id?.startsWith('deliverable-set-'));}
 click(){downloads.push({href:this.href,name:this.download});}remove(){}
}
const nodes={'task-changes-flow':new Element('div','task-changes-flow')},listeners={},calls=[],downloads=[],blobs=[];
let selected=null,profileFailure=null,detailFailure=null,lateDownload=null,detailValue={candidate_set:{members:[]},decisions:[],allowed_actions:[]};
const profile={members:[{object_ref:'quote@v1',object_digest:'digest:quote',deliverable_kind:'QUOTE'},{object_ref:'memo@v1',object_digest:'digest:memo',deliverable_kind:'DISCOUNT_MEMO'}]};
const pricing={currency:'USD',discount_rate_bps:500,discount_amount:'1.88',net_amount:'35.62',total:'42.74'};
const exported={deliverables:[{id:'quote',version:'v1',digest:'digest:quote',payload:{deliverable_kind:'QUOTE',owner:'owner:quote',pricing,product_plan:'Enterprise'}},{id:'memo',version:'v1',digest:'digest:memo',payload:{deliverable_kind:'DISCOUNT_MEMO',owner:'owner:memo',pricing,comparison:{},limitations:['NO_EXTERNAL_EFFECT'],raw_contract_text:'PRIVATE_PAYLOAD_SENTINEL'}}]};
const document={documentElement:{lang:'en'},body:new Element('body'),getElementById:id=>nodes[id]||null,createElement:tag=>new Element(tag),querySelector:selector=>selector==='#change-audit pre'&&selected?{textContent:JSON.stringify({event_id:selected})}:null};
const window={OrgRebaseClient:{session:()=>({actors:[]}),async json(path,options){calls.push({path,options});
 if(path==='/api/workspace/deliverable-set'){if(profileFailure)throw profileFailure;return structuredClone(profile);}
 if(path==='/api/workspace/export/quote')return structuredClone(exported);
 if(path==='/api/workspace/export/deliverable-set'){if(lateDownload)return lateDownload;return {schema_version:'orgrebase.deliverable-set-export.v1',deliverables:structuredClone(exported.deliverables)};}
 if(path.includes('/deliverable-set/changes/')){if(detailFailure)throw detailFailure;return structuredClone(detailValue);}
 throw new Error(path);
}},addEventListener:(type,fn)=>{(listeners[type]??=[]).push(fn);},dispatchEvent:event=>{for(const fn of listeners[event.type]||[])fn(event);}};
const CustomEvent=class{constructor(type,options={}){this.type=type;this.detail=options.detail;}};
const context=vm.createContext({window,document,CustomEvent,setTimeout:fn=>fn(),Map,Set,encodeURIComponent,Blob,URL:{createObjectURL:blob=>{blobs.push(blob);return 'blob:result';},revokeObjectURL:()=>{}}});
vm.runInContext(fs.readFileSync('demo/console/deliverable-set.js','utf8'),context);
const tick=()=>new Promise(setImmediate),text=node=>[node.textContent||'',...node.children.map(text)].join('\n');
(async()=>{
 await tick();await tick();const panel=nodes['deliverable-set-panel'];
 assert.equal(panel?.hidden??nodes['task-changes-flow'].children[0].hidden,false);
 assert(text(nodes['deliverable-set-current']).includes('Discount review memo'));
 assert(text(nodes['deliverable-set-current']).includes('USD 42.74'));
 assert(!text(nodes['deliverable-set-current']).includes('PRIVATE_PAYLOAD_SENTINEL'),'only reviewed display fields are rendered');
 assert(text(nodes['deliverable-set-notice']).includes('Current committed deliverables appear above'));
 assert.equal(nodes['deliverable-set-download'].disabled,false);
 await nodes['deliverable-set-download'].emit('click');assert.equal(downloads.length,1);
 assert.equal(downloads[0].name,'orgrebase-deliverable-set.json');assert.equal(JSON.parse(await blobs[0].text()).deliverables.length,2);
 selected='preserved:memo';
 detailValue={candidate_set:{digest:'sha256:case',members:[{object_id:'quote',deliverable_kind:'QUOTE',disposition:'REBUILD',owner_id:'owner:quote',candidate_ref:'quote@v2'},{object_id:'memo',deliverable_kind:'DISCOUNT_MEMO',disposition:'PRESERVE_WITHIN_BOUNDARY',owner_id:'owner:memo',candidate_ref:'memo@v1'}]},decisions:[],review_members:[],pending_owner_ids:['owner:quote'],current_principal_owner_id:'owner:quote',allowed_actions:['APPROVE','REJECT']};
 window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));await tick();
 const reviews=nodes['deliverable-set-members'].children.filter(n=>n.tagName==='ARTICLE');
 assert(text(reviews[0]).includes('Pending signature'),'the rebuilt Quote still needs its owner decision');
 assert(text(reviews[1]).includes('No new signature required'),'preserved Memo does not suggest Finance approval');
 assert(!text(reviews[1]).includes('Pending signature'));assert.equal(nodes['deliverable-set-members'].querySelectorAll('button').length,2,'only the current required Quote owner has approve/reject controls');
 detailValue.current_principal_owner_id='owner:memo';detailValue.allowed_actions=[];
 window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));await tick();
 assert.equal(nodes['deliverable-set-members'].querySelectorAll('button').length,0,'the preserved member gains no decision authority');
 detailValue.candidate_set.members[1].disposition='HOLD_FOR_REVIEW';
 window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));await tick();
 assert(!text(nodes['deliverable-set-members'].children[1]).includes('No new signature required'),'HOLD does not silently become preservation');
 document.documentElement.lang='zh-CN';detailValue.candidate_set.members[1].disposition='PRESERVE_WITHIN_BOUNDARY';
 window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));await tick();
 assert(text(nodes['deliverable-set-members'].children[1]).includes('不需重新签署 · 保留原版本'));
 document.documentElement.lang='en';selected=null;detailValue={candidate_set:{members:[]},decisions:[],allowed_actions:[]};
 await nodes['deliverable-set-refresh'].emit('click');
 for(const failure of [Object.assign(new Error('PRIVATE_ERROR_SENTINEL'),{status:500,code:'HTTP_500'}),Object.assign(new Error('PRIVATE_ERROR_SENTINEL'),{status:403,code:'AUTH_ACTION_DENIED'})]){
   profileFailure=failure;await nodes['deliverable-set-refresh'].emit('click');await tick();
   assert.equal(nodes['task-changes-flow'].children[0].hidden,false,'a read failure leaves a usable retry visible');
   assert.equal(nodes['deliverable-set-current'].children.length,0);assert.equal(nodes['deliverable-set-members'].children.length,0);
   assert(!nodes['deliverable-set-current-notice'].textContent.includes('PRIVATE_ERROR_SENTINEL'));assert.equal(nodes['deliverable-set-download'].disabled,true);
   profileFailure=null;await nodes['deliverable-set-refresh'].emit('click');assert(text(nodes['deliverable-set-current']).includes('USD 42.74'));
 }
 selected='event:one';detailFailure=Object.assign(new Error('PRIVATE_WAITING_SENTINEL'),{status:409,code:'WORKSPACE_PREVIEW_REQUIRED'});
 window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));await tick();
 assert(text(nodes['deliverable-set-current']).includes('USD 42.74'),'an unpreviewed change retains verified current deliverables');
 assert(nodes['deliverable-set-notice'].textContent.includes('Preview this change'),'the next action explains the normal phase');
 assert.equal(nodes['deliverable-set-members'].children.length,0,'there are no candidate decisions before preview');
 assert.equal(nodes['deliverable-set-download'].disabled,false,'current versions remain exportable');
 assert(!text(nodes['task-changes-flow']).includes('PRIVATE_WAITING_SENTINEL'));
 for(const failure of [Object.assign(new Error('PRIVATE_DETAIL_SENTINEL'),{status:403,code:'WORKSPACE_PREVIEW_REQUIRED'}),Object.assign(new Error('PRIVATE_DETAIL_SENTINEL'),{status:409,code:'DELIVERABLE_CANDIDATE_SET_MISSING'})]){
   detailFailure=failure;window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));await tick();
   assert.equal(nodes['deliverable-set-current'].children.length,0,'permission failures and unexpected conflicts stay fail-closed');
   detailFailure=null;selected=null;await nodes['deliverable-set-refresh'].emit('click');selected='event:one';
 }
 detailFailure=Object.assign(new Error('PRIVATE_DETAIL_SENTINEL'),{status:500});
 window.dispatchEvent(new CustomEvent('orgrebase:changeselection'));await tick();
 assert.equal(nodes['deliverable-set-current'].children.length,0);assert(!text(nodes['task-changes-flow']).includes('PRIVATE_DETAIL_SENTINEL'));
 detailFailure=null;selected=null;await nodes['deliverable-set-refresh'].emit('click');
 let resolve;lateDownload=new Promise(yes=>{resolve=yes;});const pending=nodes['deliverable-set-download'].emit('click');
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended'));resolve({deliverables:[]});await pending;
 assert.equal(downloads.length,1,'an old session cannot download a late result');
 profileFailure=Object.assign(new Error('unsupported'),{code:'WORKSPACE_DELIVERABLE_SET_PROFILE_NOT_CONFIGURED'});
 await nodes['deliverable-set-refresh'].emit('click');assert.equal(nodes['task-changes-flow'].children[0].hidden,true,'only an explicit unsupported profile is hidden');
})().catch(error=>{console.error(error);process.exitCode=1});
''')
