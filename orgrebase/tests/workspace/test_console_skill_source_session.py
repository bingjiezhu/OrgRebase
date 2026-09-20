from __future__ import annotations

import pytest

from tests.workspace.test_workspace_client import run_node

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require('./tests/workspace/console_dom_harness.js');
const source=fs.readFileSync('demo/console/app.js','utf8');
const html=fs.readFileSync('demo/console/index.html','utf8');
for(const match of html.matchAll(/<([a-z0-9]+)\b[^>]*\bid="(skill-source-[^"]+)"[^>]*>/g)){
 const element=document.createElement(match[1]);element.id=match[2];
 element.removeAttribute=function(key){delete this[key];delete this.attributes[key]};
}
const dialog=nodes.get('skill-source-dialog');
dialog.showModal=function(){this.open=true};dialog.close=function(){this.open=false};
const byId=id=>nodes.get(id),calls=[],toasts=[];
const session=actor=>({mode:'oidc',authenticated:true,authentication_required:true,
 principal:{tenant_id:'tenant:'+actor,actor_id:'human:skill-steward',roles:['governor'],issuer:'issuer:a',subject:'subject:'+actor}});
let currentSession=session('a');
window.OrgRebaseClient={session:()=>currentSession};
const catalog=owner=>({status:'PASS',schema_version:'orgrebase.skill-source-catalog.v1',packages:[{
 name:'enterprise-quote-compose',version:'1.3.1',package_digest:'sha256:package',skill_digest:'sha256:source',
 content:'---\nname: enterprise-quote-compose\nmetadata:\n  version: 1.3.1\n---\nPublished '+owner,
 content_bytes:100,release_state:'PUBLISHED_IMMUTABLE',editable:false,drafts:[{
 status:'DRAFT_SAVED',candidate_only:true,evaluation_status:'NOT_EVALUATED',release_status:'NOT_RELEASED',
 executable:false,registry_writes:0,canonical_target_writes:0,content:'private saved draft '+owner,
 proposed_version:'1.3.2',artifact_id:'private:'+owner}]}]});
let responder=async()=>catalog('a');
const context={document,window,byId,CustomEvent,
 t:(key,params)=>key+(params?JSON.stringify(params):''),text:(id,value)=>{byId(id).textContent=value},
 skillDisplayName:name=>name,toast:(...args)=>toasts.push(args),
 api:async(path,options={})=>{calls.push({path,options});return responder(path,options)},
 currentSkillCatalog:null,selectedSkillName:null,skillDraftEditing:false,skillDraftInFlight:false,
 skillSourceRevision:0,skillDialogStatus:{key:'skill.source.readonlyStatus',params:{},tone:'neutral'},
 workspaceSessionRevision:0,workspaceStateRequest:null,workspaceSessionIdentity:null,
 refreshState:async()=>({}),renderWorkspaceUnavailable:()=>{}};
vm.createContext(context);
const section=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
vm.runInContext(section('function normalizeSkillSourceCatalog(', '\nasync function decideExperience('),context);
vm.runInContext(section('function sessionIdentity(', '\nasync function refreshState('),context);
context.workspaceSessionIdentity=context.sessionIdentity(session('a'));
vm.runInContext(section('function invalidateWorkspaceState(', '\nwindow.addEventListener("orgrebase:sessionended"'),context);
const sessionEvents=section('function invalidateWorkspaceState(', '\nwindow.addEventListener("orgrebase:changeselection"');
for(const registration of sessionEvents.matchAll(/window.addEventListener\("(?:orgrebase:sessionended|pagehide|pageshow|orgrebase:sessionchange|orgrebase:workspacechange)",(?:[^\n]*\);|[\s\S]*?\n\}\);)/g)){
 vm.runInContext(registration[0],context);
}
const emit=(type,detail)=>{if(type==='orgrebase:sessionchange')currentSession=detail;return window.dispatchEvent(new CustomEvent(type,{detail}));};
const edit=()=>{context.openSkillSourceDialog('enterprise-quote-compose');context.beginSkillRevision()};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
'''


def test_skill_draft_clears_on_identity_change_and_recovers_without_losing_same_identity_edits():
    run_node(HARNESS + r'''
(async()=>{
 await context.loadSkillSourceCatalog();edit();byId('skill-source-editor').value='private unsaved draft a';
 emit('orgrebase:sessionchange',{...session('a'),csrf_token:'renewed'});
 emit('orgrebase:workspacechange');await tick();
 assert.equal(byId('skill-source-editor').value,'private unsaved draft a');
 assert.equal(dialog.open,true);assert.equal(calls.length,1,'normal business refresh does not reload or discard a draft');
 const next=deferred();responder=()=>next.promise;
 emit('orgrebase:sessionchange',session('b'));
 assert.equal(dialog.open,false);assert.equal(byId('skill-source-editor').value,'');
 assert.equal(byId('skill-source-proposed-version').value,'');
 assert.equal(byId('skill-source-draft-list').children.length,0);
 assert.equal(byId('skill-source-package-digest').textContent,'');
 assert.equal(context.currentSkillCatalog.packages.length,0);assert.equal(context.selectedSkillName,null);
 next.resolve(catalog('b'));await tick();
 assert.equal(byId('skill-source-catalog').dataset.state,'ready');
 assert(!JSON.stringify(context.currentSkillCatalog).includes('private saved draft a'));
 edit();assert(byId('skill-source-editor').value.includes('Published b'));
 emit('orgrebase:sessionchange',{mode:'oidc',authenticated:false,authentication_required:true,principal:null});
 emit('orgrebase:sessionended');assert.equal(dialog.open,false);
 responder=async()=>catalog('b');const before=calls.length;
 emit('orgrebase:sessionchange',session('b'));await tick();
 assert.equal(calls.length,before+1);assert.equal(context.currentSkillCatalog.status,'PASS','login recovery reloads the catalog');
 emit('pagehide');assert.equal(context.currentSkillCatalog.packages.length,0);
 window.dispatchEvent({type:'pageshow',persisted:true});await tick();
 assert.equal(context.currentSkillCatalog.status,'PASS','browser history restoration reloads cleared content');
})().catch(error=>{console.error(error);process.exitCode=1});
''')


def test_skill_catalog_ignores_late_content_after_logout_and_old_failure_after_identity_change():
    run_node(HARNESS + r'''
(async()=>{
 const old=deferred();responder=()=>old.promise;const pending=context.loadSkillSourceCatalog();
 emit('orgrebase:sessionended');old.resolve(catalog('a'));await pending;
 assert.equal(context.currentSkillCatalog.packages.length,0);
 assert.equal(byId('skill-source-editor').value,'');
 const failed=deferred();responder=()=>failed.promise;const obsolete=context.loadSkillSourceCatalog();
 responder=async()=>catalog('b');emit('orgrebase:sessionchange',session('b'));await tick();
 failed.reject(new Error('private old failure'));await obsolete;
 assert.equal(context.currentSkillCatalog.status,'PASS');
 assert.equal(context.currentSkillCatalog.packages[0].drafts[0].content,'private saved draft b');
 assert.equal(byId('skill-source-catalog').dataset.state,'ready');
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.parametrize('identity_field', ['issuer', 'subject'])
def test_skill_source_clears_when_oidc_identity_changes_with_same_tenant_actor(identity_field: str):
    run_node(HARNESS + f"\nconst identityField='{identity_field}';\n" + r'''
(async()=>{
 await context.loadSkillSourceCatalog();edit();byId('skill-source-editor').value='private unsaved draft a';
 const next=session('a');next.principal[identityField]='replacement';
 const pending=deferred();responder=()=>pending.promise;
 emit('orgrebase:sessionchange',next);
 assert.equal(dialog.open,false);assert.equal(byId('skill-source-editor').value,'');
 assert.equal(context.currentSkillCatalog.packages.length,0);
 assert.equal(calls.length,2,'new OIDC identity rereads the catalog despite equal tenant and actor');
 pending.resolve(catalog('b'));await tick();assert.equal(context.currentSkillCatalog.status,'PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
''')


@pytest.mark.parametrize('phase', ['post', 'readback'])
@pytest.mark.parametrize('outcome', ['success', 'failure'])
def test_old_skill_save_cannot_restore_text_or_unlock_the_new_identity_save(phase: str, outcome: str):
    run_node(HARNESS + f"\nconst phase='{phase}',outcome='{outcome}';\n" + r'''
(async()=>{
 await context.loadSkillSourceCatalog();edit();
 const old=deferred();
 responder=async(path,options)=>options.method==='POST'
   ? phase==='post'?old.promise:{proposed_version:'1.3.2'} : old.promise;
 const oldSave=context.saveSkillRevisionDraft();await tick();
 assert.equal(context.skillDraftInFlight,true);
 responder=async()=>catalog('b');emit('orgrebase:sessionchange',session('b'));await tick();edit();
 byId('skill-source-editor').value+='\nprivate unsaved draft b';
 const current=deferred();responder=async(path,options)=>options.method==='POST'?current.promise:catalog('b');
 const currentSave=context.saveSkillRevisionDraft();await tick();const before=calls.length;
 if(outcome==='failure')old.reject(new Error('private old failure'));
 else old.resolve(phase==='post'?{proposed_version:'1.3.2'}:catalog('a'));
 await oldSave;
 assert.equal(calls.length,before,'old POST completion cannot request another catalog');
 assert.equal(context.skillDraftInFlight,true);assert.equal(byId('skill-source-save').disabled,true);
 assert(byId('skill-source-editor').value.includes('private unsaved draft b'));
 assert.equal(context.currentSkillCatalog.packages[0].drafts[0].content,'private saved draft b');
 assert.equal(toasts.length,0);assert.equal(context.skillDialogStatus.tone,'neutral');
 current.resolve({proposed_version:'1.3.2'});await currentSave;
 assert.equal(context.skillDraftInFlight,false);assert.equal(byId('skill-source-save').disabled,false);
 assert.equal(context.skillDialogStatus.tone,'success');assert.equal(toasts.length,1);
 assert.equal(calls.filter(call=>call.options.method==='POST').length,2,'identity change never replays an old save');
})().catch(error=>{console.error(error);process.exitCode=1});
''')


def test_skill_source_is_readable_but_not_editable_by_a_finance_approver():
    run_node(HARNESS + r'''
(async()=>{
 await context.loadSkillSourceCatalog();
 currentSession={...session('a'),principal:{tenant_id:'tenant:a',actor_id:'finance',roles:['approver']}};
 context.openSkillSourceDialog('enterprise-quote-compose');
 assert.equal(byId('skill-source-revise').disabled,true);
 context.beginSkillRevision();
 assert.equal(context.skillDraftEditing,false);
 await context.saveSkillRevisionDraft();
 assert.equal(calls.filter(call=>call.options.method==='POST').length,0);
 assert(byId('skill-source-editor').value.includes('Published a'));
})().catch(error=>{console.error(error);process.exitCode=1});
''')
