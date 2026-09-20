"""Explicit evidence retries recover failed reads without replaying business work."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/app.js','utf8');
const html=fs.readFileSync('demo/console/index.html','utf8');
assert(html.includes('id="current-run-evidence-retry-panel" hidden'));
const panel={hidden:true},button={disabled:true,addEventListener(type,fn){assert.equal(type,'click');this.click=fn;}};
const calls=[],commandRenders=[],notifications=[],state={execution:{run_id:'run:completed'},key:'same-completion'};
const context=vm.createContext({currentState:state,currentRunArchive:null,currentRunArchiveRequestedFor:null,
 workspaceSessionRevision:0,
 CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
 window:{dispatchEvent(event){assert.equal(event.type,'orgrebase:staterendered');
  assert.equal(event.detail,context.currentState);notifications.push(event);}},
 currentRunObservability:null,currentRunObservabilityRequestedFor:null,
 terminalRunId:s=>s?.execution.run_id||null,currentRunCompletionKey:s=>s?.key||null,
 currentRunArchiveProof:v=>v?.status==='VERIFIED',renderOperations(){},renderCurrentRunArchive(){},
 renderCurrentRunValue(){},renderAcceptanceStory(){},renderCurrentTaskBadge(){},
 renderCommand(value){assert.equal(value,context.currentState);commandRenders.push(value);},
 verifyArchiveApprovalAuthorities:async()=>{},verifyCompletedRunObservability:async()=>{},
 byId:id=>id==='current-run-evidence-retry-panel'?panel:button,
 api(path,options){assert(['/api/workspace/run-archive','/api/workspace/run-observability'].includes(path));
  assert.equal(options?.method||'GET','GET');return new Promise((resolve,reject)=>calls.push({path,resolve,reject}));}});
vm.runInContext(source.slice(source.indexOf('function refreshCompletedRunObservabilityProjection('),
 source.indexOf('function renderWorkspaceUnavailable(')),context);
const binding=source.match(/byId\("current-run-evidence-retry"\)\.addEventListener\("click", retryCurrentRunEvidence\);/);
assert(binding,'the visible button is wired to the read-only retry');vm.runInContext(binding[0],context);
const tick=()=>new Promise(setImmediate);
const fail=request=>request.reject(Object.assign(new Error('temporary network failure'),{status:503}));
'''


def test_explicit_retry_recovers_archive_and_deduplicates_inflight_reads():
    run_node(HARNESS + r'''
(async()=>{
 context.refreshCurrentRunArchive();fail(calls[0]);await tick();
 assert.equal(panel.hidden,false);assert.equal(button.disabled,false);
 context.refreshCurrentRunArchive();assert.equal(calls.length,1,'rendering does not automatically retry');
 button.click();button.click();assert.equal(calls.length,2,'one user retry starts one read');
 assert.equal(panel.hidden,true);assert.equal(button.disabled,true);
 calls[1].resolve({status:'VERIFIED'});await tick();assert.equal(calls.length,3);
 assert.equal(calls[2].path,'/api/workspace/run-observability');
 calls[2].resolve({status:'VERIFIED'});await tick();
 context.refreshCurrentRunArchive();button.click();assert.equal(calls.length,3,'successful evidence is reused');
 assert.equal(panel.hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_observability_retry_preserves_successful_archive_and_remains_manual_after_failure():
    run_node(HARNESS + r'''
(async()=>{
 context.refreshCurrentRunArchive();const archive={status:'VERIFIED'};
 calls[0].resolve(archive);await tick();fail(calls[1]);await tick();
 assert.equal(panel.hidden,false);button.click();button.click();
 assert.equal(context.currentRunArchive,archive);assert.equal(calls.length,3);
 assert.equal(calls[2].path,'/api/workspace/run-observability','retry only the failed read');
 fail(calls[2]);await tick();assert.equal(panel.hidden,false);
 context.refreshCurrentRunArchive();assert.equal(calls.length,3,'failed retries do not start a loop');
 button.click();calls[3].resolve({status:'VERIFIED'});await tick();
 assert.equal(panel.hidden,true);assert.equal(context.currentRunArchive,archive);
 assert.equal(calls.filter(call=>call.path.endsWith('run-archive')).length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_old_run_failure_cannot_offer_or_publish_a_retry_into_new_context():
    run_node(HARNESS + r'''
(async()=>{
 context.refreshCurrentRunArchive();fail(calls[0]);await tick();button.click();
 context.currentState={execution:{run_id:'run:other'},key:'other-completion'};
 context.renderCurrentRunEvidenceRetry();button.click();assert.equal(calls.length,2);
 const renders=commandRenders.length,events=notifications.length;
 calls[1].resolve({status:'VERIFIED',marker:'old'});await tick();
 assert.equal(context.currentRunArchive,null);assert.equal(panel.hidden,true);
 assert.equal(commandRenders.length,renders);assert.equal(notifications.length,events);
 context.currentState=null;context.renderCurrentRunEvidenceRetry();button.click();assert.equal(calls.length,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


@pytest.mark.parametrize("target", ["archive", "observability"])
@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_previous_identity_cannot_overwrite_new_identity_evidence_for_the_same_run(target, outcome):
    run_node(HARNESS + r'''
(async()=>{
 context.refreshCurrentRunArchive();
 if(TARGET==='observability'){calls[0].resolve({status:'VERIFIED'});await tick();}
 const previous=calls.at(-1);
 context.workspaceSessionRevision++;
 context.currentRunArchive=null;context.currentRunArchiveRequestedFor=null;
 context.currentRunObservability=null;context.currentRunObservabilityRequestedFor=null;
 context.refreshCurrentRunArchive();
 const freshArchive={status:'VERIFIED',marker:'current identity'};
 calls.at(-1).resolve(freshArchive);await tick();
 const freshObservability={status:'VERIFIED',marker:'current identity'};
 calls.at(-1).resolve(freshObservability);await tick();
 const renders=commandRenders.length,events=notifications.length;
 if(OUTCOME==='failure')fail(previous);else previous.resolve({status:'VERIFIED',marker:'old identity'});
 await tick();assert.equal(context.currentRunArchive,freshArchive);
 assert.equal(context.currentRunObservability,freshObservability);assert.equal(panel.hidden,true);
 assert.equal(commandRenders.length,renders);assert.equal(notifications.length,events);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("TARGET", repr(target)).replace("OUTCOME", repr(outcome)))
