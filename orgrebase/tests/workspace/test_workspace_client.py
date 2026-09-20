from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "demo/console"


def run_node(program: str) -> None:
    wire = r'''
const fs=require('node:fs');
globalThis.crypto=require('node:crypto').webcrypto;
require('node:vm').runInThisContext(fs.readFileSync('demo/console/wire.js','utf8'));
async function wireResponse(value, options={}) {
 const headers=new Headers(options.headers||{});
 headers.set('OrgRebase-Wire-Scheme',OrgRebaseWire.scheme);
 headers.set('OrgRebase-Wire-Digest',await OrgRebaseWire.digest(value));
 return Response.json(value,{...options,headers});
}
'''
    program = wire + program.replace("Response.json(", "wireResponse(").replace(
        "vm.runInNewContext(", "window.OrgRebaseWire=globalThis.OrgRebaseWire;\nvm.runInNewContext("
    )
    result = subprocess.run([shutil.which("node") or "node", "-e", program],
                            cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_session_transport_enforces_cookie_csrf_and_never_replays_expired_commands() -> None:
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    run_node(r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const events = [];
const calls = [];
const active = { mode: "oidc", authentication_required: true, authenticated: true,
  principal: { actor_id: "owner:1", tenant_id: "tenant:1", roles: ["approver"] },
  csrf_token: "csrf-one", login_url: "/api/session/login", logout_url: "/api/session/logout", expires_at: 100 };
let nextSession = active;
let commandStatus = 200;
const document = { documentElement: {lang: "en"}, getElementById: () => null };
const window = {
  location: { origin: "https://work.example" },
  addEventListener() {},
  dispatchEvent(event) { events.push(event); },
  async fetch(path, options) {
    calls.push({path, options});
    if (path === "/api/session") return Response.json(nextSession);
    if (path === "/api/workspaces") return Response.json({items:[{workspace_id:"default",label:"Default"}],default_workspace_id:"default"});
    if (path === "/api/session/logout") return Response.json({...active, authenticated: false, principal: null, csrf_token: null});
    return Response.json({ok: commandStatus === 200}, {status: commandStatus});
  },
};
const CustomEvent = class { constructor(type, options = {}) {this.type=type;this.detail=options.detail;} };
vm.runInNewContext(SOURCE, {window, document, URL, Headers, CustomEvent});
(async () => {
  const client = window.OrgRebaseClient;
  await client.refreshSession();
  assert.equal(calls.filter(c => c.path === "/api/session").length, 1);
  await client.fetch("/api/workspace/state");
  assert.equal(calls.at(-1).options.headers.has("X-CSRF-Token"), false);
  assert.equal(calls.at(-1).options.headers.get("X-OrgRebase-Workspace"), "default");
  await client.json("/api/workspace/changes", {method:"POST",headers:{"X-OrgRebase-Actor":"forged"},body:{event_id:"one"}});
  assert.equal(calls.at(-1).options.headers.get("X-CSRF-Token"), "csrf-one");
  assert.equal(calls.at(-1).options.headers.has("X-OrgRebase-Actor"), false);
  assert.equal(calls.at(-1).options.credentials, "same-origin");
  assert.equal(calls.at(-1).options.body, '{"event_id":"one"}');
  const beforeForeign = calls.length;
  await assert.rejects(client.fetch("https://other.example/api/workspace/state"), /ORIGIN_DENIED/);
  assert.equal(calls.length, beforeForeign);
  commandStatus = 401;
  const beforeExpired = calls.length;
  await assert.rejects(client.json("/api/workspace/changes", {method:"POST",body:{event_id:"two"}}));
  assert.equal(calls.length, beforeExpired + 1, "expired command is never replayed");
  assert.equal(client.session().authenticated, false);
  assert(events.some(e => e.type === "orgrebase:sessionended"));
  await assert.rejects(client.json("/api/workspace/changes", {method:"POST",body:{event_id:"two"}}));
  assert.equal(calls.length, beforeExpired + 1, "signed-out command must not reach the server");
  nextSession = {...active, csrf_token:"csrf-two"};
  await client.refreshSession(); commandStatus = 200;
  assert.equal(calls.filter(c => c.path === "/api/workspace/changes").length, 2, "session refresh is not a command retry");
  await client.logout();
  assert.equal(calls.at(-1).path, "/api/session/logout");
  assert.equal(calls.at(-1).options.headers.get("X-CSRF-Token"), "csrf-two");
  assert.equal(client.session().authenticated, false);
})().catch(error => {console.error(error);process.exitCode=1;});
'''.replace("SOURCE", source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_local_session_keeps_explicit_fixture_identity_and_session_failure_stops_writes() -> None:
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    run_node(r'''
const assert = require("node:assert/strict");const vm=require("node:vm");
const CustomEvent = class {constructor(type,options){this.type=type;this.detail=options?.detail;}};
async function check(unavailable) {
  const calls=[];
  const document={documentElement:{lang:"en"},getElementById:()=>null};
  const window={location:{origin:"http://localhost"},addEventListener(){},dispatchEvent(){},
    async fetch(path,options){calls.push({path,options});return path==="/api/session"
      ? unavailable ? Response.json({}, {status:503}) : Response.json({mode:"local",authentication_required:false,authenticated:false,principal:null,csrf_token:null,login_url:null,logout_url:null})
      : path==="/api/workspaces" ? Response.json({items:[{workspace_id:"default",label:"Default"}],default_workspace_id:"default"}) : Response.json({ok:true});}};
  vm.runInNewContext(SOURCE,{window,document,URL,Headers,CustomEvent});
  const client=window.OrgRebaseClient;
  if(unavailable){await assert.rejects(client.json("/api/workspace/changes",{method:"POST",body:{}}));
    assert.equal(calls.filter(c=>c.path!=="/api/session").length,0);}
  else {await client.json("/api/workspace/approve/one",{method:"POST",headers:{"X-OrgRebase-Actor":"owner:fixture"},body:{}});
    assert.equal(calls.at(-1).options.headers.get("X-OrgRebase-Actor"),"owner:fixture");
    assert.equal(calls.at(-1).options.headers.has("X-CSRF-Token"),false);}
}
(async()=>{await check(false);await check(true);})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace("SOURCE", source))


def test_every_console_network_entry_uses_the_shared_session_transport() -> None:
    import re
    for path in CONSOLE.glob("*.js"):
        if path.name == "workspace-client.js":
            continue
        for match in re.finditer(r"(?<![\w.])(?:window\.)?fetch\s*\(", path.read_text()):
            pytest.fail(f"Unmediated browser request in {path.name}:{match.start()}")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_local_role_switch_rotates_identity_and_cannot_reuse_pending_responses() -> None:
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    harness = json.dumps(str(CONSOLE.parents[1] / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
const calls=[],events=[];
for(const id of ['workspace-session','session-status','session-description','session-login',
 'session-logout','session-refresh','session-actor-picker','session-actor-summary','session-actor-label',
 'session-actor-note','session-actor-switch','session-actor']) {
 const el=document.createElement(id==='session-actor'?'select':'div');el.id=id;
}
window.location={origin:'http://127.0.0.1:8030'};
const actors=[{actor_id:'sales',label:'业务发起人',label_en:'Business requester',roles:['operator']},
 {actor_id:'finance',label:'财务负责人',label_en:'Finance owner',roles:['approver']}];
let current={mode:'local',identity_source:'controlled-local-session',authentication_required:true,
 authenticated:false,principal:null,actors,csrf_token:null,switch_actor_url:'/api/session/local-actor'};
let pending=null,expireNext=false;
window.addEventListener('orgrebase:sessionended',event=>events.push(event.detail.reason));
window.fetch=async(path,options)=>{
 calls.push({path,options});
 if(path==='/api/session')return Response.json(current);
 if(path==='/api/session/local-actor'){
  if(current.csrf_token){
   assert.equal(options.headers.get('X-CSRF-Token'),current.csrf_token);
   assert.equal(options.headers.has('X-OrgRebase-Local-Session'),false);
  }else{
   assert.equal(options.headers.get('X-OrgRebase-Local-Session'),'initialize');
   assert.equal(options.headers.has('X-CSRF-Token'),false);
  }
  const actor=actors.find(item=>item.actor_id===JSON.parse(options.body).actor_id);
  current={...current,authenticated:true,principal:{...actor,tenant_id:'tenant'},csrf_token:actor.actor_id+'-csrf'};
  return Response.json(current);
 }
 if(path==='/api/workspaces')return Response.json({items:[{workspace_id:'default',label:'Default'}],default_workspace_id:'default'});
 if(path==='/api/workspace/state')return new Promise(resolve=>{pending=resolve;});
 if(expireNext){
  expireNext=false;current={...current,authenticated:false,principal:null,csrf_token:null};
  return Response.json({detail:{code:'AUTH_LOCAL_SESSION_REQUIRED'}},{status:401});
 }
 return Response.json({ok:true});
};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,URL,Headers});
(async()=>{
 const client=window.OrgRebaseClient;await client.refreshSession();
 assert.equal(nodes.get('session-actor-switch').disabled,false,'first selection must not require an existing cookie');
 const before=calls.length;
 await assert.rejects(client.json('/api/workspace/changes',{method:'POST',body:{}}),/AUTH_SESSION_REQUIRED|Sign in again/);
 assert.equal(calls.length,before,'no business request before a role is selected');
 nodes.get('session-actor').value='sales';await nodes.get('session-actor-switch').emit('click');
 assert.equal(client.session().principal.actor_id,'sales');
 await client.json('/api/workspace/changes',{method:'POST',headers:{'X-OrgRebase-Actor':'finance','X-OrgRebase-Local-Session':'initialize'},body:{event_id:'one'}});
 assert.equal(calls.at(-1).options.headers.has('X-OrgRebase-Actor'),false,'actor header cannot impersonate a different role');
 assert.equal(calls.at(-1).options.headers.get('X-CSRF-Token'),'sales-csrf');
 assert.equal(calls.at(-1).options.headers.has('X-OrgRebase-Local-Session'),false,'business commands cannot request identity initialization');
 const stale=client.json('/api/workspace/state');
 const rejected=assert.rejects(stale,/WORKSPACE_REQUEST_CONTEXT_CHANGED/);await tick();
 nodes.get('session-actor').value='finance';await nodes.get('session-actor-switch').emit('click');
 pending(await Response.json({private_draft:'sales-only'}));await rejected;
 assert.equal(client.session().principal.actor_id,'finance');
 assert.deepEqual(events,['role-switch','role-switch']);
 assert.equal(calls.filter(item=>item.path==='/api/workspace/changes').length,1,'switching does not replay any command');
 assert.equal(nodes.get('session-status').textContent,'Finance owner');
 expireNext=true;
 await assert.rejects(client.json('/api/workspace/changes',{method:'POST',body:{event_id:'expired'}}),/AUTH_LOCAL_SESSION_REQUIRED/);
 assert.equal(client.session().authenticated,false);
 assert.equal(client.session().csrf_token,null);
 assert.equal(nodes.get('session-actor-switch').disabled,false,'expiry must allow a new explicit identity selection');
 const expiredCalls=calls.length;
 await assert.rejects(client.json('/api/workspace/changes',{method:'POST',body:{event_id:'expired'}}),/AUTH_SESSION_REQUIRED|Sign in again/);
 assert.equal(calls.length,expiredCalls,'expired business commands stay blocked');
 nodes.get('session-actor').value='sales';await nodes.get('session-actor-switch').emit('click');
 assert.equal(client.session().principal.actor_id,'sales','explicit selection recovers without replaying business work');
 assert.equal(calls.at(-1).options.headers.get('X-OrgRebase-Local-Session'),'initialize');
 assert.equal(calls.filter(item=>item.path==='/api/workspace/changes').length,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_workspace_selection_uses_authorized_catalog_and_reload_clears_old_page() -> None:
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes}=require(HARNESS);const calls=[];let reloads=0,saved='not-authorized',catalogAvailable=true;
for(const id of ['session-workspace','session-workspace-picker','session-workspace-switch']){const node=document.createElement(id==='session-workspace'?'select':'button');node.id=id;}
window.location={origin:'https://work.example',reload(){reloads++;}};
window.sessionStorage={getItem(){return saved;},setItem(key,value){assert.equal(key,'orgrebase.workspace');saved=value;}};
window.fetch=async(path,options)=>{calls.push({path,options});
 if(path==='/api/session')return Response.json({mode:'local',authentication_required:false,authenticated:false,principal:null});
 if(path==='/api/workspaces')return catalogAvailable?Response.json({items:[{workspace_id:'quote-a',label:'First quote'},{workspace_id:'quote-b',label:'Second quote'}],default_workspace_id:'quote-a'}):Response.json({}, {status:503});
 return Response.json({ok:true});};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,URL,Headers});
(async()=>{
 const client=window.OrgRebaseClient;await client.json('/api/workspace/state',{headers:{'X-OrgRebase-Workspace':'not-authorized'}});
 assert.equal(calls.at(-1).options.headers.get('X-OrgRebase-Workspace'),'quote-a','untrusted header and storage cannot select a hidden workspace');
 assert.equal(calls.find(item=>item.path==='/api/workspaces').options.headers.has('X-OrgRebase-Workspace'),false);
 assert.equal(nodes.get('session-workspace').children.length,2);
 nodes.get('session-workspace').value='quote-b';await nodes.get('session-workspace-switch').emit('click');
 assert.equal(saved,'quote-b');assert.equal(reloads,1,'switch uses a clean page, without carrying previews or drafts');
 assert.equal(calls.filter(item=>item.path==='/api/workspace/state').length,1,'switch does not replay any business operation');
 catalogAvailable=false;await assert.rejects(client.refreshWorkspaces(),/CATALOG_UNAVAILABLE/);
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_old_session_commands_cannot_restore_candidates_or_revoke_a_new_session():
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    shell = (CONSOLE / "workspace-shell.js").read_text()
    helpers = shell[shell.index("  async function taskIntakeApi("):shell.index("  function taskWorkDescriptionKey(")]
    prepare = shell[shell.index("  async function prepareTaskIntake()"):shell.index("  async function admitTaskIntake()")]
    run_node(r'''
const assert=require('node:assert/strict'), vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const active={mode:'oidc',authentication_required:true,authenticated:true,
 principal:{actor_id:'actor:A',tenant_id:'tenant:A'},csrf_token:'csrf:A',logout_url:'/api/session/logout'};
let identity=active;const pending=[];const calls=[];
const document={documentElement:{lang:'en'},getElementById:()=>null};
const listeners=new Map();const window={location:{origin:'https://work.example'},
 addEventListener(name,fn){listeners.set(name,[...(listeners.get(name)||[]),fn]);},
 dispatchEvent(event){for(const fn of listeners.get(event.type)||[])fn(event);},
 async fetch(path,options){calls.push({path,options});
  if(path==='/api/session')return Response.json(identity);
  if(path==='/api/session/logout')return Response.json({...active,authenticated:false,principal:null,csrf_token:null});
  if(path==='/api/workspaces')return Response.json({items:[{workspace_id:'default',label:'Default'}],default_workspace_id:'default'});
  return new Promise(resolve=>pending.push({resolve,path}));
 }};
const CustomEvent=class{constructor(type,options={}){this.type=type;this.detail=options.detail;}};
vm.runInNewContext(SOURCE,{window,document,URL,Headers,CustomEvent});
(async()=>{
 const client=window.OrgRebaseClient;await client.refreshSession();await client.refreshWorkspaces();
 const prompt={value:'Actor A request'};
 const state={window,currentState:{stage:'EMPTY',actor:'actor:A'},taskIntakeBusy:false,taskCandidate:null,
  taskApproval:null,taskIntakeError:null,taskIntakeAction:'idle',
  byId:()=>prompt,organizationContractReady:()=>true,navigate(){},
  taskScope:value=>({actorId:value.actor,customerId:'customer',deliverableKind:'quote'}),
  renderTaskIntake(){},renderContextFacts(){},renderOverviewFlow(){},
  safeTaskIntakeError:error=>error.code,taskIntakeHeaders:actor=>({'X-OrgRebase-Actor':actor}),
 };
 const context=vm.createContext(state);vm.runInContext(HELPERS+PREPARE,context);
 const preparing=context.prepareTaskIntake();await tick();
 const oldCommand=pending.shift();assert.equal(oldCommand.path,'/api/workspace/task-intake/prepare');
 await client.logout();context.taskCandidate=null;
 identity={...active,principal:{actor_id:'actor:B',tenant_id:'tenant:B'},csrf_token:'csrf:B'};
 await client.refreshSession();context.currentState={stage:'EMPTY',actor:'actor:B'};
 oldCommand.resolve(await Response.json({status:'READY_FOR_CONFIRMATION',actor_id:'actor:A',digest:'sha256:old'}));
 await preparing;
 assert.equal(context.taskCandidate,null,'an old actor candidate must not reappear after logout');
 assert.equal(client.session().principal.actor_id,'actor:B');
 assert.equal(calls.filter(item=>item.path.includes('/task-intake/prepare')).length,1,'no automatic command replay');

 await client.refreshWorkspaces();
 const stale=client.json('/api/workspace/changes',{method:'POST',body:{event_id:'old'}});
 const rejected=assert.rejects(stale,/WORKSPACE_REQUEST_CONTEXT_CHANGED/);await tick();
 const late401=pending.shift();identity={...active,principal:{actor_id:'actor:C',tenant_id:'tenant:C'},csrf_token:'csrf:C'};
 await client.refreshSession();late401.resolve(await Response.json({}, {status:401}));await rejected;
 assert.equal(client.session().principal.actor_id,'actor:C','a late old 401 must not revoke the new session');
 assert.equal(client.session().authenticated,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("SOURCE", source).replace("HELPERS", json.dumps(helpers)).replace("PREPARE", json.dumps(prepare)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_body_decode_after_session_change_cannot_publish_private_text_or_download():
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    shell = (CONSOLE / "workspace-shell.js").read_text()
    private_loader = shell[shell.index("  function taskWorkDescriptionKey("):shell.index("  function setFormedWorkspaceVisibility(")]
    private_loader = shell[shell.index("  function taskScope("):shell.index("  function displayTaskActor(")] + private_loader
    app = (CONSOLE / "app.js").read_text()
    download_start = app.index("async function downloadJson(")
    download = app[download_start:app.index('document.querySelectorAll(".language-option")', download_start)]
    api = app[app.index("function errorMessage("):app.index("function activePreview(")]
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const active={mode:'oidc',authentication_required:true,authenticated:true,principal:{actor_id:'A',tenant_id:'A'},csrf_token:'A'};
let identity=active,finishBody,bodyStarted,downloads=0;
const receipt={run_id:'run',digest:'receipt:A',prompt_digest:'p',prompt_length:7,candidate_digest:'c',approval_digest:'a',formation_receipt_digest:'f'};
const payload={...receipt,schema_version:'orgrebase.workspace-task-intake-work-description-record.v1',status:'PRIVATE_RECORD_RETAINED',actor_id:'A',semantic_use:'TASK_INTENT_ONLY',contributes_business_facts:false,grants_authority:false,included_in_public_evidence:false,included_in_events_or_otlp:false,work_description:'private A request'};
const document={documentElement:{lang:'en'},getElementById:()=>null,body:{append(){}},createElement(){downloads++;return {click(){},remove(){}};}};
const window={location:{origin:'https://work.example'},addEventListener(){},dispatchEvent(){},
 async fetch(path){
  if(path==='/api/session')return Response.json(identity);
  if(path==='/api/workspaces')return Response.json({items:[{workspace_id:'default',label:'Default'}],default_workspace_id:'default'});
  if(path==='/api/error')return Response.json({detail:{code:'REVIEW_GATE_NOT_READY',remaining_ms:321,not_before:1234}}, {status:409});
  const raw=await Response.json(payload);
  return {ok:true,status:200,headers:raw.headers,clone:()=>raw.clone(),
   json(){bodyStarted();return new Promise(resolve=>{finishBody=()=>resolve(payload);});},
   blob(){bodyStarted();return new Promise(resolve=>{finishBody=()=>resolve(new Blob(['private export']));});}};
 }};
const CustomEvent=class{constructor(type,options={}){this.type=type;this.detail=options.detail;}};
vm.runInNewContext(__CLIENT_SOURCE__,{window,document,URL,Headers,CustomEvent});
(async()=>{
 const client=window.OrgRebaseClient;await client.refreshSession();await client.refreshWorkspaces();
 const state={window,document,currentState:{execution:{run_id:'run'},task_intake:receipt,enterprise_data_lineage:{source:{task:{actor_id:'A'}}}},taskWorkDescription:null,taskWorkDescriptionRunId:null,taskWorkDescriptionStatus:'idle',taskWorkDescriptionContext:null,taskWorkDescriptionGeneration:0,
  renderTaskIntake(){},taskIntakeHeaders:()=>({}),inFlight:false,toast(){},t:(key,values)=>`${key}:${JSON.stringify(values)}`,URL:{createObjectURL:()=>"blob:test",revokeObjectURL(){}}};
 const context=vm.createContext(state);vm.runInContext(__PRIVATE_LOADER__+__APP_API__+__DOWNLOAD__,context);
 let started=new Promise(resolve=>{bodyStarted=resolve;});
 const reading=context.loadTaskWorkDescription(context.currentState,receipt,'A');await started;
 identity={...active,principal:{actor_id:'B',tenant_id:'B'},csrf_token:'B'};await client.refreshSession();
 context.currentState={execution:{run_id:'run:B'},task_intake:{...receipt,run_id:'run:B',digest:'receipt:B'},enterprise_data_lineage:{source:{task:{actor_id:'B'}}}};finishBody();await reading;
 assert.equal(context.taskWorkDescription,null,'a delayed old private body must not be installed');
 assert.notEqual(context.taskWorkDescriptionStatus,'available');
 await client.refreshWorkspaces();
 started=new Promise(resolve=>{bodyStarted=resolve;});
 const exporting=context.downloadJson('/api/workspace/export/quote','quote.json');await started;
 identity={...active,principal:{actor_id:'C',tenant_id:'C'},csrf_token:'C'};await client.refreshSession();
 finishBody();await exporting;
 assert.equal(downloads,0,"a delayed old blob must not start a download");
 await assert.rejects(context.api('/api/error'),error=>{
  assert.equal(error.httpStatus,409);assert.equal(error.remainingMs,321);assert.equal(error.notBefore,1234);
  assert.equal(error.code,'REVIEW_GATE_NOT_READY');assert.match(error.message,/error.apiCode/);return true;
 });
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("__CLIENT_SOURCE__",source).replace("__PRIVATE_LOADER__",json.dumps(private_loader)).replace("__APP_API__",json.dumps(api)).replace("__DOWNLOAD__",json.dumps(download)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_role_switch_invalidates_old_401_before_switch_response_and_blocks_interim_reads():
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    harness = json.dumps(str(ROOT / "tests/workspace/console_dom_harness.js"))
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(HARNESS);
for(const id of ['workspace-session','session-status','session-description','session-login',
 'session-logout','session-refresh','session-actor-picker','session-actor-summary','session-actor-label',
 'session-actor-note','session-actor-switch','session-actor']) {
 const el=document.createElement(id==='session-actor'?'select':'div');el.id=id;
}
window.location={origin:'http://127.0.0.1:8030'};
const actors=[{actor_id:'sales',label:'Business requester',roles:['operator']},
 {actor_id:'finance',label:'Finance owner',roles:['approver']}];
const identity=actor=>({mode:'local',identity_source:'controlled-local-session',authentication_required:true,
 authenticated:true,principal:{...actor,tenant_id:'tenant'},actors,csrf_token:actor.actor_id+'-csrf',
 switch_actor_url:'/api/session/local-actor'});
let current=identity(actors[0]),oldResponse,switchResponse;const calls=[],reloaded=[];
window.fetch=async(path,options)=>{
 calls.push({path,options});
 if(path==='/api/session')return Response.json(current);
 if(path==='/api/workspaces')return Response.json({items:[{workspace_id:'default',label:'Default'}],default_workspace_id:'default'});
 if(path==='/api/workspace/changes')return new Promise(resolve=>{oldResponse=resolve;});
 if(path==='/api/session/local-actor')return new Promise(resolve=>{switchResponse=resolve;});
 return Response.json({ok:true});
};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,URL,Headers});
(async()=>{
 const client=window.OrgRebaseClient;await client.refreshSession();
 await client.refreshWorkspaces();
 const old=client.json('/api/workspace/changes');
 const oldRejected=assert.rejects(old,/WORKSPACE_REQUEST_CONTEXT_CHANGED/);await tick();
 nodes.get('session-actor').value='finance';const changing=nodes.get('session-actor-switch').emit('click');await tick();
 assert.equal(typeof switchResponse,'function');
 const before=calls.length;
 await assert.rejects(client.json('/api/workspace/state'),/WORKSPACE_REQUEST_CONTEXT_CHANGED/);
 await assert.rejects(client.refreshSession(),/WORKSPACE_REQUEST_CONTEXT_CHANGED/);
 assert.equal(calls.length,before,'no reads can create another old-role request during a switch');
 oldResponse(await Response.json({detail:{code:'AUTH_LOCAL_SESSION_REQUIRED'}},{status:401}));await oldRejected;
 assert.equal(client.session().principal.actor_id,'sales','old 401 is ignored even before new identity arrives');
 window.addEventListener('orgrebase:workspacechange',()=>{reloaded.push(client.json('/api/workspace/state'));});
 current=identity(actors[1]);switchResponse(await Response.json(current));await changing;
 assert.equal(client.session().principal.actor_id,'finance');
 assert.equal(client.session().authenticated,true);
 assert.equal(reloaded.length,1);await Promise.all(reloaded);
 assert.equal(calls.filter(call=>call.path==='/api/workspace/state').length,1,'reads resume when the new identity is published');
 assert.equal(calls.filter(call=>call.path==='/api/session/local-actor').length,1,'switch is never replayed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace('HARNESS', harness).replace('SOURCE', source))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("cookie_rotated", [False, True])
@pytest.mark.parametrize("failure_status", [0, 401, 403])
@pytest.mark.parametrize("recheck_fails", [False, True])
def test_uncertain_role_switch_reconciles_without_replay_and_retires_drafts(
    cookie_rotated, failure_status, recheck_fails,
):
    source = json.dumps((CONSOLE / "workspace-client.js").read_text())
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes}=require('./tests/workspace/console_dom_harness.js');
for(const id of ['workspace-session','session-status','session-description','session-login',
 'session-logout','session-refresh','session-actor-picker','session-actor-summary','session-actor-label',
 'session-actor-note','session-actor-switch','session-actor']) {
 const el=document.createElement(id==='session-actor'?'select':'div');el.id=id;
}
window.location={origin:'http://127.0.0.1:8030'};
const actors=[{actor_id:'sales',label:'Business requester',roles:['operator']},
 {actor_id:'finance',label:'Finance owner',roles:['approver']}];
const identity=actor=>({mode:'local',identity_source:'controlled-local-session',authentication_required:true,
 authenticated:true,principal:{...actor,tenant_id:'tenant'},actors,csrf_token:actor.actor_id+'-csrf',
 switch_actor_url:'/api/session/local-actor'});
let server=identity(actors[0]),firstRead=true,failRead=RECHECK_FAILS;const calls=[],ended=[];
window.addEventListener('orgrebase:sessionended',event=>ended.push(event.detail.reason));
window.fetch=async(path,options)=>{
 calls.push({path,options});
 if(path==='/api/session'){
  if(!firstRead && failRead)return Response.json({},{status:503});
  firstRead=false;return Response.json(server);
 }
 if(path==='/api/session/local-actor'){
  if(COOKIE_ROTATED)server=identity(actors[1]);
  if(FAILURE_STATUS)return Response.json({detail:{code:'AUTH_LOCAL_SWITCH_DENIED'}},{status:FAILURE_STATUS});
  throw new TypeError('Response lost');
 }
 throw new Error('Unexpected business request: '+path);
};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,URL,Headers});
(async()=>{
 const client=window.OrgRebaseClient;await client.refreshSession();
 nodes.get('session-actor').value='finance';await nodes.get('session-actor-switch').emit('click');
 assert(ended.includes('role-switch'),'an uncertain switch must retire all old-role drafts, not just expired views');
 assert.equal(calls.filter(call=>call.path==='/api/session/local-actor').length,1,'the switch must not be replayed');
 assert.equal(calls.filter(call=>call.path==='/api/session').length,2,'only a read may reconcile the cookie');
 if(RECHECK_FAILS){
  assert.equal(client.session(),null,'no old identity may survive a failed recheck');
  assert.equal(nodes.get('session-refresh').hidden,false,'session recovery remains accessible');
  assert.equal(nodes.get('session-actor-picker').hidden,true,'identity must be verified before another switch');
  assert(nodes.get('session-description').textContent.includes('unconfirmed'));
  failRead=false;
  await nodes.get('session-refresh').emit('click');
 }
 assert.equal(client.session().principal.actor_id,COOKIE_ROTATED?'finance':'sales');
 assert.equal(client.session().csrf_token,server.csrf_token);
 assert.equal(calls.filter(call=>call.options.method==='POST').length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("SOURCE", source).replace("COOKIE_ROTATED", json.dumps(cookie_rotated))
        .replace("FAILURE_STATUS", str(failure_status)).replace("RECHECK_FAILS", json.dumps(recheck_fails)))
