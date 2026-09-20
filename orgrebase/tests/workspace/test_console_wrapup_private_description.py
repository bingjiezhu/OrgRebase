from __future__ import annotations

import json
import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const loader=source.slice(source.indexOf('  function taskScope('),source.indexOf('  function displayTaskActor('))
 +source.slice(source.indexOf('  function taskWorkDescriptionKey('),source.indexOf('  function setFormedWorkspaceVisibility('));
let workspace='workspace:A';const requests=[];
function state(run,label=run,actor='actor:A'){
 return {execution:{run_id:run},enterprise_data_lineage:{source:{task:{actor_id:actor}}},
  task_intake:{run_id:run,digest:'receipt:'+label,prompt_digest:'prompt:'+label,prompt_length:9,
    candidate_digest:'candidate:'+label,approval_digest:'approval:'+label,formation_receipt_digest:'formation:'+label}};
}
const first=state('run:A');
const context=vm.createContext({currentState:first,taskWorkDescription:null,taskWorkDescriptionRunId:null,
 taskWorkDescriptionStatus:'idle',taskWorkDescriptionContext:null,taskWorkDescriptionGeneration:0,taskIntakeHeaders:()=>({}),renderTaskIntake(){},
 window:{OrgRebaseClient:{workspace:()=>workspace,json(){return new Promise((resolve,reject)=>requests.push({resolve,reject}));}}}});
vm.runInContext(loader,context);
function payload(selected,text){return {...selected.task_intake,
 schema_version:'orgrebase.workspace-task-intake-work-description-record.v1',status:'PRIVATE_RECORD_RETAINED',
 actor_id:selected.enterprise_data_lineage.source.task.actor_id,semantic_use:'TASK_INTENT_ONLY',
 contributes_business_facts:false,grants_authority:false,included_in_public_evidence:false,
 included_in_events_or_otlp:false,work_description:text};}
'''


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("changed", ["run", "receipt", "actor", "workspace", "unavailable"])
@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_old_private_description_response_cannot_change_current_task(changed, outcome):
    run_node(HARNESS + r'''
(async()=>{
 const pending=context.loadTaskWorkDescription(first,first.task_intake,'actor:A');
 assert.equal(requests.length,1);
 const changes={run:state('run:B'),receipt:state('run:A','revised'),actor:state('run:A','run:A','actor:B'),
  workspace:first,unavailable:null};
 context.currentState=changes[CHANGED];
 if(CHANGED==='workspace')workspace='workspace:B';
 context.taskWorkDescriptionRunId=context.currentState?.execution.run_id || null;
 context.taskWorkDescription='Current task text';context.taskWorkDescriptionStatus='available';
 if(OUTCOME==='success')requests[0].resolve(payload(first,'Old private A text'));
 else requests[0].reject(Object.assign(new Error('MISSING'),{status:404}));
 await pending;
 assert.equal(context.taskWorkDescription,'Current task text','old success cannot publish into a new task');
 assert.equal(context.taskWorkDescriptionStatus,'available','old failure cannot reset the new task');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("CHANGED", json.dumps(changed)).replace("OUTCOME", json.dumps(outcome)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_private_description_rejects_old_scheduled_read_and_loads_exact_current_receipt():
    run_node(HARNESS + r'''
(async()=>{
 const next=state('run:B');context.currentState=next;
 await context.loadTaskWorkDescription(first,first.task_intake,'actor:A');
 assert.equal(requests.length,0,'a timer for the old task must not start its read');
 const current=context.loadTaskWorkDescription(next,next.task_intake,'actor:A');
 assert.equal(requests.length,1);
 requests[0].resolve(payload(next,'Current B request'));await current;
 assert.equal(context.taskWorkDescriptionRunId,'run:B');
 assert.equal(context.taskWorkDescription,'Current B request');
 assert.equal(context.taskWorkDescriptionStatus,'available');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')
