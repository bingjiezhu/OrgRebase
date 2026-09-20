"""Failed formation remains visible without replaying or discarding authority."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")


def test_failed_task_returns_to_visible_error_after_normal_hash_navigation() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const queue=[],listeners=[],calls=[],focus=[];
let hash='#agents';
const error={hidden:true,textContent:'',focus(){assert.equal(context.currentRoute,'quote');
 assert.equal(context.activeTabs.quote,'work');assert.equal(this.hidden,false);focus.push(this.textContent);},
 scrollIntoView(){}};
const prompt={value:'Existing reviewed request'};
const candidate={status:'READY_FOR_CONFIRMATION',digest:'candidate:exact'},approval={digest:'approval:exact'};
const context=vm.createContext({window:{OrgRebaseClient:{workspace:()=> 'workspace:one'},location:{get hash(){return hash;},set hash(value){hash=value;
 queue.push(()=>{context.activate(context.routeFromHash(),{focus:true});
 for(const entry of listeners.splice(0))entry();});}},
 addEventListener(type,callback,options){assert.equal(type,'hashchange');assert.equal(options.once,true);listeners.push(callback);},
 dispatchEvent(event){calls.push(event.type);}},CustomEvent:class {constructor(type){this.type=type;}},
 ROUTES:[{id:'onboarding'},{id:'quote'},{id:'assurance'}],ROUTE_ALIASES:{agents:'quote'},
 SUBROUTE_TABS:{quote:'work',agents:'collaboration'},activeTabs:{quote:'work'},currentRoute:'quote',
 currentState:{stage:'EMPTY'},STAGES:['EMPTY','CURRENT'],taskIntakeBusy:false,taskIntakeAction:'idle',
 taskIntakeError:null,taskCandidate:candidate,taskApproval:approval,runProgressFollowing:false,
 byId:id=>id==='task-request-prompt'?prompt:id==='task-intake-error'?error:null,
 taskScope:()=>({actorId:'owner:one'}),safeTaskIntakeError:()=> 'Review the organization contract before retrying.',
 renderTaskIntake(){error.hidden=!context.taskIntakeError;error.textContent=context.taskIntakeError||'';},
 activateShellTab(route,tab){context.activeTabs[route]=tab;},
 activate(route){context.currentRoute=route;context.activateShellTab(route,context.activeTabs[route]);},
 followRunProgress(){},async refreshRunProgress(){},
 async taskIntakeApi(path,body){calls.push({path,body});throw Object.assign(new Error('redacted'),{code:'OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED'});},
});
vm.runInContext(source.slice(source.indexOf('  function routeFromHash('),source.indexOf('  function routeDescriptor('))
 + source.slice(source.indexOf('  function navigate('),source.indexOf('  function renderMaterials('))
 + source.slice(source.indexOf('  function persistedTaskIntake('),source.indexOf('  function renderTaskIntake('))
 + source.slice(source.indexOf('  async function runTaskIntake('),source.indexOf('  function bindTaskIntake(')),context);
(async()=>{
 await context.runTaskIntake();
 assert.equal(context.taskCandidate,candidate);assert.equal(context.taskApproval,approval);
 assert.equal(context.taskIntakeBusy,false);assert.equal(prompt.value,'Existing reviewed request');
 assert.equal(queue.length,1);assert.equal(focus.length,0,'defer focus until the real route handler completes');
 queue.shift()();assert.equal(context.activeTabs.quote,'work');assert.equal(error.hidden,false);
 assert.equal(focus.length,1);assert.equal(calls.filter(item=>item.path).length,1,'failure does not rerun formation');
 context.taskIntakeError=null;context.renderTaskIntake();
 await context.runTaskIntake();assert.equal(focus.length,2,'canonical hash focuses synchronously');
 assert.equal(queue.length,0);
 context.taskIntakeApi=async()=>{throw Object.assign(new Error('stale'),{code:'WORKSPACE_REQUEST_CONTEXT_CHANGED'});};
 await context.runTaskIntake();assert.equal(focus.length,2,'discarded requests must not navigate or focus a new workspace');
 assert.equal(context.taskIntakeError,null);
 context.taskIntakeApi=async()=>({state:{stage:'CURRENT'}});
 await context.runTaskIntake();assert.equal(context.activeTabs.quote,'collaboration','successful formation stays in progress');
 assert.equal(focus.length,2);assert(calls.includes('orgrebase:workspacechange'));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_exact_identifiers_are_available_without_replacing_readable_copy() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const context=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('  function setExactText('),source.indexOf('  function renderContextFacts(')),context);
const node={textContent:'',attributes:{},setAttribute(key,value){this.attributes[key]=value;},removeAttribute(key){delete this.attributes[key];}};
const exact='run:one · sha256:'+'a'.repeat(64);
context.setExactText(node,'Verified task',exact);
assert.equal(node.textContent,'Verified task');assert.equal(node.attributes.title,exact);
for(const empty of [undefined,null,'',' ']){
 context.setExactText(node,'Verified task',exact);context.setExactText(node,'Waiting',empty);
 assert.equal(node.textContent,'Waiting');assert.equal(node.attributes.title,undefined,'new empty projections cannot retain a stale identifier');
}
context.setExactText(null,'No node',exact);
''')
