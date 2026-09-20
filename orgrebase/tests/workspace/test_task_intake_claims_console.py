"""A formed Quote is not evidence that an employee confirmed its originating task."""

from __future__ import annotations

import json
import shutil

import pytest

from tests.workspace.test_workspace_client import run_node


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_intake_heading_requires_the_same_persisted_receipt_as_the_evidence_panel(language):
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const nodes=new Map(),scheduled=[];
const byId=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},value:'',hidden:false,disabled:false,
 textContent:'',removeAttribute(){}});return nodes.get(id);};
const context=vm.createContext({language:LANGUAGE,byId,
 window:{OrgRebaseClient:{workspace:()=> 'workspace:one',
  session:()=>({mode:'oidc',authentication_required:true,authenticated:true,principal:{actor_id:'employee:one'}})},
  setTimeout:callback=>{scheduled.push(callback);return scheduled.length;}},
 STAGES:['EMPTY','CURRENT','PREVIEWED','APPROVED','RECOVERY_REQUIRED'],stateAvailability:'ready',currentState:null,
 taskWorkDescriptionRunId:null,taskWorkDescriptionStatus:'idle',taskWorkDescription:null,taskWorkDescriptionContext:null,taskWorkDescriptionGeneration:0,
 taskIntakeBusy:false,taskIntakeAction:'idle',taskCandidate:null,taskApproval:null,taskIntakeError:null,
 taskScope:()=>({actorId:'employee:one',customerId:'customer:one',deliverableKind:'Quote',templateRef:'template:one'}),
 organizationContractReady:()=>true,organizationContractEstablished:()=>true,setFormedWorkspaceVisibility(){},
 displayTaskActor:value=>value,displayTaskCustomer:value=>value,taskIntakeIssues:()=>[],
 setExactText:(node,value)=>{node.textContent=value;},format:(template,values)=>Object.entries(values||{})
  .reduce((result,[key,value])=>result.replaceAll('{'+key+'}',String(value)),template),
});
vm.runInContext(source.slice(source.indexOf('  const COPY ='),source.indexOf('  const byId ='))
 +'\nfunction copy(){return COPY[language];}',context);
vm.runInContext(source.slice(source.indexOf('  function taskWorkDescriptionKey('),source.indexOf('  async function loadTaskWorkDescription('))
 +source.slice(source.indexOf('  const DIGEST_PATTERN ='),source.indexOf('  async function prepareTaskIntake(')),context);
const digest='sha256:'+'a'.repeat(64);
// Schema-shaped synthetic projection for this renderer test, not a claimed live task.
const receipt={schema_version:'orgrebase.workspace-task-intake-run-receipt.v1',status:'FORMATION_COMPLETED',
 run_id:'run:one',actor_id:'employee:one',intake_persisted:true,intake_canonical_target_writes:0,
 formation_authority:'ORGREBASE_CONTROL_PLANE',claim_boundary:'INTAKE_GATE_VERIFIED_THEN_EXISTING_CONTROL_PLANE_FORMED_QUOTE',
 prompt_length:20,quote_ref:'quote:one@v1',artifact_id:'task-intake:one',digest,prompt_digest:digest,
 candidate_digest:digest,approval_digest:digest,task_digest:digest,formation_receipt_digest:digest,
 artifact_payload_digest:digest,event_digest:digest};
const state={stage:'CURRENT',execution:{run_id:'run:one'},quote:{version:'v1'},task_intake:null};
const labels=context.copy();
for(const invalid of [null,{...receipt,run_id:'run:other'},{...receipt,event_digest:null},{...receipt,intake_persisted:false}]){
 state.task_intake=invalid;context.renderTaskIntake(state);
 assert.equal(byId('task-intake-kicker').textContent,labels.taskUnverifiedKicker);
 assert.equal(byId('task-intake-title').textContent,labels.taskUnverifiedTitle);
 assert.equal(byId('task-intake-body').textContent,labels.taskEvidenceUnavailableDetail);
 assert.equal(byId('task-intake-candidate-status').textContent,labels.taskEvidenceUnavailable);
 assert.notEqual(byId('task-intake-title').textContent,labels.taskCompletedTitle);
 assert.equal(byId('task-intake-run').hidden,true,'existing quote cannot start a second formation');
 assert.equal(byId('task-intake-admit').hidden,true);assert.equal(byId('task-request-prompt').disabled,true);
 assert.equal(scheduled.length,0,'unbound intake cannot request private work-description text');
}
state.task_intake=receipt;context.renderTaskIntake(state);
assert.equal(byId('task-intake-kicker').textContent,labels.taskCompletedKicker);
assert.equal(byId('task-intake-title').textContent,labels.taskCompletedTitle);
assert.equal(byId('task-intake-body').textContent,labels.taskCompletedBody);
assert.equal(byId('task-intake-candidate-status').textContent,labels.taskStarted);
assert.equal(scheduled.length,1);
context.renderTaskIntake({stage:'EMPTY',execution:{run_id:'run:new'},task_intake:null});
assert.equal(byId('task-intake-title').textContent,labels.taskIntakeTitle);
assert.equal(byId('task-intake-body').textContent,labels.taskIntakeBody);
assert.equal(byId('task-request-prompt').disabled,false);
'''.replace("LANGUAGE", json.dumps(language)))
