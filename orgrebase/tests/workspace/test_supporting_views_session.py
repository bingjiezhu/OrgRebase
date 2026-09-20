from __future__ import annotations

import json

from tests.workspace.test_workspace_client import CONSOLE, run_node


def test_supporting_views_wait_for_identity_and_discard_previous_identity_responses():
    source = (CONSOLE / "app.js").read_text()
    source = source[source.index("let supportingViewsRevision = 0;"):source.index("function readinessProbeResult(")]
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {window,CustomEvent,tick}=require('./tests/workspace/console_dom_harness.js');
let session={mode:'local',authentication_required:true,authenticated:false,principal:null};
window.OrgRebaseClient={session:()=>session};
const pending=[],rendered=[];
const context={window,currentState:null,UNAVAILABLE_WORKSPACE_PROJECTION:{},
 currentReleaseFacts:null,currentOperatingModel:null,
 api:path=>new Promise(resolve=>pending.push({path,resolve})),
 renderSemifinalEvidence:value=>rendered.push(['archive',value]),
 renderPublicRealProcessValidation:value=>rendered.push(['public',value]),
 renderValueAndResponsibility:value=>rendered.push(['model',value]),renderCurrentProofOverview(){}};
vm.createContext(context);vm.runInContext(SOURCE,context);
(async()=>{
 assert.equal(pending.length,0,'initial signed-out page must not request private work');
 session={...session,authenticated:true,principal:{actor_id:'sales'}};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 assert.equal(pending.length,4,'first identity selection loads all supporting views');
 const first=pending.splice(0);
 session={...session,principal:{actor_id:'finance'}};
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'role-switch'}}));
 window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:session}));
 assert.equal(pending.length,4);
 for(const request of pending.splice(0))request.resolve({status:'PASS',identity:'finance'});
 await tick();
 for(const request of first)request.resolve({status:'PASS',identity:'sales'});
 await tick();
 assert.equal(rendered.filter(([,v])=>v.identity==='finance').length,3);
 assert.equal(rendered.filter(([,v])=>v.identity==='sales').length,0,'late old responses cannot overwrite the new role');
 assert.equal(context.currentReleaseFacts.identity,'finance');
 assert.equal(context.currentOperatingModel.identity,'finance');
 window.dispatchEvent(new CustomEvent('orgrebase:sessionended',{detail:{reason:'logout'}}));
 assert.equal(context.currentReleaseFacts.identity,undefined);
 assert.equal(context.currentOperatingModel.status,'UNAVAILABLE');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace("SOURCE", json.dumps(source)))
