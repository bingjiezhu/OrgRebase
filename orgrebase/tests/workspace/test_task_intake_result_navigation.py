"""Navigation follows a bound completed receipt, never a late or unrelated result."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_task_intake_result_navigation_preserves_request_bindings():
    script = r'''
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const funcs=source.slice(source.indexOf('  function persistedTaskIntake('),source.indexOf('  function renderTaskIntake('))
 +source.slice(source.indexOf('  async function runTaskIntake('),source.indexOf('  function focusTaskIntakeError('));
const digest='sha256:'+'a'.repeat(64), approval='sha256:'+'b'.repeat(64);
async function check(mode){
 let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});let workspace='one';const tabs=[],posts=[];
 const candidate={status:'READY_FOR_CONFIRMATION',digest,intended_run_id:'run:one',workspace_instance_nonce:'nonce:one'};
 const granted={digest:approval,intended_run_id:'run:one',workspace_instance_nonce:'nonce:one'};
 const receipt={schema_version:'orgrebase.workspace-task-intake-run-receipt.v1',status:'FORMATION_COMPLETED',run_id:'run:one',actor_id:'actor:one',intake_persisted:true,intake_canonical_target_writes:0,formation_authority:'ORGREBASE_CONTROL_PLANE',claim_boundary:'INTAKE_GATE_VERIFIED_THEN_EXISTING_CONTROL_PLANE_FORMED_QUOTE',prompt_length:10,quote_ref:'quote:one@v1',artifact_id:'intake:one',digest,prompt_digest:digest,candidate_digest:digest,approval_digest:approval,task_digest:digest,formation_receipt_digest:digest,artifact_payload_digest:digest,event_digest:digest};
 const formed={stage:'CURRENT',execution:{run_id:'run:one'},quote:{ref:'quote:one@v1'},task_intake:receipt};
 const c={taskIntakeBusy:false,taskCandidate:candidate,taskApproval:granted,taskIntakeError:null,taskIntakeAction:'idle',currentRoute:'quote',currentState:{stage:'EMPTY',execution:{run_id:'run:one'}},STAGES:['EMPTY','CURRENT'],DIGEST_PATTERN:/^sha256:[a-f0-9]{64}$/,
 taskScope:()=>({actorId:'actor:one'}),byId:()=>({value:'retained prompt'}),renderTaskIntake:()=>{},activateShellTab:(route,tab)=>tabs.push(tab),followRunProgress:()=>{},refreshRunProgress:async()=>{},runProgressFollowing:true,safeTaskIntakeError:()=> 'safe error',focusTaskIntakeError:()=>{},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},window:{OrgRebaseClient:{workspace:()=>workspace},dispatchEvent:()=>{}},taskIntakeApi:(path)=>{posts.push(path);return promise;}};
 vm.createContext(c);vm.runInContext(funcs,c);const pending=c.runTaskIntake();assert.deepEqual(tabs,['collaboration']);
 if(mode==='wrong-run')receipt.run_id='run:other';
 if(mode==='changed-run')c.currentState.execution.run_id='run:new';
 if(mode==='workspace')workspace='two';
 if(mode==='nonce')granted.workspace_instance_nonce='nonce:other';
 if(mode==='candidate')receipt.candidate_digest=approval;
 if(mode==='replaced-approval')c.taskApproval={...granted};
 if(mode==='missing-receipt')formed.task_intake=null;
 if(mode==='different-page')c.currentRoute='onboarding';
 if(mode==='late-context')reject(Object.assign(new Error('context changed'),{code:'WORKSPACE_REQUEST_CONTEXT_CHANGED'}));
 else resolve(mode==='flat-success'?formed:{state:formed});
 await pending;assert.equal(posts.length,1);
 assert.deepEqual(tabs,['success','flat-success'].includes(mode)?['collaboration','work']:['collaboration'],mode);
}
(async()=>{for(const mode of ['success','flat-success','wrong-run','changed-run','workspace','nonce','candidate','replaced-approval','missing-receipt','different-page','late-context'])await check(mode);})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, text=True,
                            capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
