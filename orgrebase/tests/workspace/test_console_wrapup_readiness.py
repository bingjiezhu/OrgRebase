from __future__ import annotations

import json
import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const calls=[];let probeStatus=200,probeBody={status:'ready'},probeNetworkError=false,probeMalformed=false,probeWaitForAbort=false;
let sessionError=true;
const document={documentElement:{lang:'en'},getElementById:()=>null};
const window={location:{origin:'https://local.example'},addEventListener(){},dispatchEvent(){},async fetch(path,options){
 calls.push({path,options});
 if(path==='/api/session')return Response.json(sessionError?{status:'unavailable'}:{mode:'local',authenticated:false,authentication_required:false},{status:sessionError?503:200});
 if(path==='/readyz'){
  if(probeWaitForAbort)return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('PROBE_ABORTED')),{once:true}));
  if(probeNetworkError)throw new Error('NETWORK_DOWN');
  if(probeMalformed)return new Response('not JSON',{status:probeStatus});
  return Response.json(probeBody,{status:probeStatus});
 }
 if(path==='/api/health')return Response.json({status:'ok'});
 if(path==='/api/workspaces')return Response.json({items:[{workspace_id:'default',label:'Default'}],default_workspace_id:'default'});
 return Response.json({ok:true});
}};
const CustomEvent=class {constructor(type,options={}){this.type=type;this.detail=options.detail;}};
const context=vm.createContext({window,document,URL,Headers,CustomEvent,
 t:key=>key,displayToken:value=>value,projectedEventScopes:()=>({global:{}}),exactAuditTitle(){}});
vm.runInContext(fs.readFileSync('demo/console/workspace-client.js','utf8'),context);
const app=fs.readFileSync('demo/console/app.js','utf8');
vm.runInContext(app.slice(app.indexOf('function shortDigest('),app.indexOf('function escapeHtml('))
 + app.slice(app.indexOf('function errorMessage('),app.indexOf('function activePreview('))
 + app.slice(app.indexOf('function readinessProbeResult('),app.indexOf('const OPERATIONS_HEALTH_REFRESH_MS')),context);
const rendered={};context.text=context.localizedText=(key,value)=>{rendered[key]=value;};
vm.runInContext(app.slice(app.indexOf('function renderOperations('),app.indexOf('  const workspaceLoading =',app.indexOf('function renderOperations(')))+'}',context);
async function readReadiness(){
 const [result]=await Promise.allSettled([context.api('/readyz')]);
 const value=context.readinessProbeResult(result);
 context.currentHealth={status:'ok'};context.currentReadiness=value;context.renderOperations({});
 return value;
}
'''


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize(
    ("setup", "expected", "label", "detail"),
    [
        ("", "ready", "READY", "operations.ready.confirmed"),
        ("probeBody={status:'not_ready'};", "not_ready", "operations.ready.notReady", "operations.ready.notReadyDetail"),
        ("probeStatus=503;probeBody={detail:{code:'STORE_NOT_READY'}};", "not_ready", "operations.ready.notReady", "operations.ready.notReadyDetail"),
        ("probeNetworkError=true;", "unavailable", "UNAVAILABLE", "operations.ready.unavailable"),
        ("probeMalformed=true;", "unavailable", "UNAVAILABLE", "operations.ready.unavailable"),
        ("probeStatus=503;probeMalformed=true;", "unavailable", "UNAVAILABLE", "operations.ready.unavailable"),
        ("probeStatus=503;probeBody=null;", "unavailable", "UNAVAILABLE", "operations.ready.unavailable"),
        ("probeBody={status:'unknown'};", "unavailable", "UNAVAILABLE", "operations.ready.unavailable"),
        ("probeStatus=500;", "unavailable", "UNAVAILABLE", "operations.ready.unavailable"),
    ],
)
def test_readiness_uses_received_probe_status_even_if_session_is_unavailable(setup, expected, label, detail):
    run_node(HARNESS + "\n(async()=>{\n" + setup + r'''
 const result=await readReadiness();
 assert.equal(result.status,EXPECTED);
 assert.equal(rendered['ops-ready'],LABEL);
 assert.equal(rendered['ops-ready-detail'],DETAIL);
 const probes=calls.filter(call=>call.path==='/readyz');
 assert.equal(probes.length,1,'no probe retry or session dependency');
 assert.equal(probes[0].options.method,'GET');assert.equal(probes[0].options.cache,'no-store');
 assert.equal(new Headers(probes[0].options.headers).has('X-CSRF-Token'),false);
 assert.equal(new Headers(probes[0].options.headers).has('X-OrgRebase-Workspace'),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("EXPECTED", json.dumps(expected)).replace("LABEL", json.dumps(label)).replace("DETAIL", json.dumps(detail)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_probe_exception_is_same_origin_exact_get_and_does_not_relax_private_wire_checks():
    run_node(HARNESS + r'''
(async()=>{
 const client=window.OrgRebaseClient;
 await client.refreshSession().catch(()=>{});
 for(const [path,method] of [['https://other.example/readyz','GET'],['/readyz/extra','GET'],['/readyz','POST'],['/readyz','HEAD'],['/private','GET']]){
  const before=calls.length;
  await assert.rejects(client.fetch(path,{method}),/WORKSPACE_REQUEST_ORIGIN_DENIED/);
  assert.equal(calls.length,before);
 }
 assert.equal((await context.api('/api/health')).status,'ok');
 const response=await client.fetch('/readyz');
 sessionError=false;await client.refreshSession();
 assert.equal((await client.readBody(response)).status,'ready','public status is independent of later session changes');
 let verified=0;window.OrgRebaseWire={verifyResponse:async()=>{verified++;throw new Error('WIRE_REJECTED');}};
 await assert.rejects(client.json('/api/workspace/state'),/WIRE_REJECTED/);
 assert.equal(verified,1,'business responses still require Wire verification');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_public_probe_transports_cancellation_without_forwarding_private_headers():
    run_node(HARNESS + r'''
(async()=>{
 probeWaitForAbort=true;
 const controller=new AbortController();
 const pending=window.OrgRebaseClient.fetch('/readyz',{signal:controller.signal,
  headers:{'X-CSRF-Token':'must-not-forward','X-OrgRebase-Workspace':'must-not-forward'}});
 const call=calls.at(-1);assert.equal(call.path,'/readyz');assert.equal(call.options.signal,controller.signal);
 assert.equal(call.options.method,'GET');assert.equal(call.options.cache,'no-store');
 assert.deepEqual(Object.keys(call.options.headers),['Accept']);
 controller.abort();await assert.rejects(pending,/PROBE_ABORTED/);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')
