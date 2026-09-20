"""Keep formation observations separate from one proposal's business authority."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


def test_authority_ladder_uses_exact_proposal_receipts_without_borrowing_formation() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the shipped browser renderer")
    script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("demo/console/app.js", "utf8");
const fixture = JSON.parse(fs.readFileSync("evidence/golden-competition/latest/pilot/state.json", "utf8"));
const nodes = new Map(["authority-ladder", "authority-ladder-context", "authority-ladder-steps", "business-change-board"]
  .map(id => [id, { hidden: true, textContent: "", innerHTML: "" }]));
let observations;
let selection = null;
const context = vm.createContext({
  window: { OrgRebaseChangeWorkbench: { selectedDetail: () => selection } },
  byId: id => nodes.get(id), text: (id, value) => { nodes.get(id).textContent = value; },
  t: (key, args) => JSON.stringify([key, args]), displayToken: value => value,
  quoteRevisionLabel: value => value,
  authorityStep: (label, observed, detail) => { observations[label] = { observed, detail }; return label; },
});
for (const [start, end] of [
  ["function currentRunId(state)", "function exactTaskInvocation"],
  ["function activeCompetition(state)", "function stateExecution"],
  ["function renderAuthorityLadder(state)", "const DATA_JOURNEY_SLOT_KEYS"],
  ["function renderBusinessChangeBoard(state)", "function selectiveRound"],
]) vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), context);
function current() {
  const state = structuredClone(fixture);
  const eventId = "ui:arbitrary-product-change";
  const change = state.changes.currency;
  for (const record of Object.values(change)) if (record) record.kind = eventId;
  change.approval.binding.change_kind = eventId;
  state.schema_version = "orgrebase.workspace-state.v2";
  state.changes = { [eventId]: change };
  state.change_events = [{ event_id: eventId, event_digest: "sha256:" + "a".repeat(64), status: "APPLIED" }];
  state.active_event_id = null;
  return state;
}
function render(state) {
  observations = {};
  context.renderBusinessChangeBoard(state);
  return Object.fromEntries(Object.entries(observations).map(([key, value]) => [key.split(".").at(-1), value.observed]));
}
const complete = { at: true, reviewer: true, candidate: true, human: true, canonical: true };
const state = current();
assert.deepEqual(render(state), complete);
assert.equal(nodes.get("authority-ladder").hidden, false, "the current v2 product must expose this view");
assert.equal(nodes.get("business-change-board").hidden, true, "the old two-round board stays retired");
assert(nodes.get("authority-ladder-context").textContent.includes("ui:arbitrary-product-change"));

const deterministic = current();
delete deterministic.competition_evidence;
assert.deepEqual(render(deterministic), { ...complete, at: false, reviewer: false },
  "actual business receipts are not conditional on the optional formation demonstration");

const directPass = current();
const reviewer = directPass.competition_evidence.agent_collaboration.reviewer;
reviewer.attempt_1 = { verdict: "PASS", missing_domains: [], reason_codes: ["COMPLETE_DECLARED_SCOPE"] };
delete reviewer.attempt_2;
assert.deepEqual(render(directPass), complete, "passing once must not require a staged failure first");

const replan = current();
delete replan.competition_evidence.agent_collaboration.reviewer.attempt_2;
assert.deepEqual(render(replan), { ...complete, reviewer: false }, "received REPLAN is not candidate acceptance");

const pending = current();
pending.active_event_id = "new:pending";
pending.changes["new:pending"] = { preview: null, approval: null, outcome: null };
pending.change_events.push({ event_id: "new:pending", status: "RECEIVED" });
assert.deepEqual(render(pending), { ...complete, candidate: false, human: false, canonical: false },
  "latest receipts from the prior proposal cannot complete an active new proposal");
assert(nodes.get("authority-ladder-context").textContent.includes("new:pending"));

const previewOnly = current();
Object.values(previewOnly.changes)[0].approval = null;
Object.values(previewOnly.changes)[0].outcome = null;
previewOnly.change_events[0].status = "PREVIEWED";
assert.deepEqual(render(previewOnly), { ...complete, human: false, canonical: false },
  "a verified preview awaiting its first approval is valid and must not crash the workspace renderer");

for (const mutate of [
  change => { change.approval.binding.change_kind = "other:event"; },
  change => { change.approval.binding.workflow_run_id = "run:other"; },
  change => { change.approval.binding.run_nonce = "other-nonce"; },
  change => { change.approval.approval.preview_digest = "sha256:other"; },
]) {
  const invalid = current(); mutate(Object.values(invalid.changes)[0]);
  assert.deepEqual(render(invalid), { ...complete, human: false, canonical: false });
}
for (const mutate of [
  change => { change.outcome.kind = "other:event"; },
  change => { change.outcome.outcome.rebase_receipt.workflow_run_id = "run:other"; },
  change => { change.outcome.outcome.rebase_receipt.run_nonce = "other-nonce"; },
  change => { change.outcome.outcome.rebase_receipt.status = "FAILED"; },
  change => { change.outcome.outcome.workspace_rebase_receipt.successor_object_refs = ["quote:other@v1"]; },
]) {
  const invalid = current(); mutate(Object.values(invalid.changes)[0]);
  assert.deepEqual(render(invalid), { ...complete, canonical: false });
}
const historical = current();
historical.quote.version = "v4";
assert.deepEqual(render(historical), complete, "a historical application keeps its own successor receipt");
assert(observations["authorityLadder.canonical"].detail.includes('"quote":"v3"'));
assert(observations["authorityLadder.canonical"].detail.includes('"current":"v4"'));
const selectedState = current();
const selectedDigest = "sha256:" + "b".repeat(64);
selectedState.changes.launch_date = structuredClone(fixture.changes.launch_date);
selectedState.change_events.unshift({ event_id: "launch_date", event_digest: selectedDigest, status: "APPLIED" });
selection = { ...selectedState.changes.launch_date, execution_run_id: selectedState.execution.run_id,
  event: { event_id: "launch_date", digest: selectedDigest }, status: "APPLIED" };
assert.deepEqual(render(selectedState), complete);
assert(observations["authorityLadder.canonical"].detail.includes('"quote":"v2"'));
assert(nodes.get("authority-ladder-context").textContent.includes("authorityLadder.selectedContext"));
assert(nodes.get("authority-ladder-context").textContent.includes("launch_date"));
assert.deepEqual(render(structuredClone(selectedState)), complete, "polling preserves the workbench selection");
assert(observations["authorityLadder.canonical"].detail.includes('"quote":"v2"'));
for (const mutate of [
  value => { value.execution_run_id = "run:foreign"; },
  value => { value.event.digest = "sha256:" + "c".repeat(64); },
]) {
  const prior = selection; selection = structuredClone(prior); mutate(selection);
  render(selectedState);
  assert(!nodes.get("authority-ladder-context").textContent.includes("authorityLadder.selectedContext"));
  assert(observations["authorityLadder.canonical"].detail.includes('"quote":"v3"'));
  selection = prior;
}
observations = {};
context.renderAuthorityLadder({ execution: { run_id: "UNAVAILABLE" } });
assert.equal(nodes.get("authority-ladder").hidden, true);
assert(!nodes.get("authority-ladder-context").textContent.includes("ui:arbitrary-product-change"));
assert(Object.values(observations).every(value => !value.observed));
assert(source.slice(source.indexOf("function renderWorkspaceUnavailable"), source.indexOf("function render(state)"))
  .includes("renderAuthorityLadder(UNAVAILABLE_WORKSPACE_PROJECTION)"));
'''
    result = subprocess.run([node, "-e", script], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


PROGRESS_RENDERER = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/app.js','utf8');
const nodes=new Map(['authority-ladder','authority-ladder-title','authority-ladder-context',
 'authority-ladder-steps','business-change-board'].map(id=>[id,{hidden:true,textContent:'',innerHTML:''}]));
const digest='sha256:'+'a'.repeat(64),otherDigest='sha256:'+'b'.repeat(64);
let selected={execution_run_id:'run:current',event:{event_id:'change:current',digest},
 status:'PREVIEWED',preview:null,approval:null,outcome:null};
let progress={execution_run_id:'run:current',event_id:'change:current',event_digest:digest,
 steps:['proposal','agentteams','candidate','review','human','effect'].map((key,index)=>({key,
  status:['complete','current','waiting','blocked','unobserved','waiting'][index],text:'CURRENT_CHANGE_'+key}))};
let helperCalls=0,fallback=[];
const context=vm.createContext({
 DEFAULT_LANGUAGE:'zh-CN',currentLanguage:'en',window:{OrgRebaseChangeWorkbench:{selectedDetail:()=>selected,
  authorityProgress:()=>{helperCalls++;return progress&&{...progress,steps:progress.steps.map(step=>({...step,
   label:(context.currentLanguage==='en'?'Current change ':'本次变更 ')+step.key}))};}}},
 byId:id=>nodes.get(id),text:(id,value)=>{nodes.get(id).textContent=value;},displayToken:value=>value,
 t:(key,args)=>context.currentLanguage+':'+key+JSON.stringify(args||{}),quoteRevisionLabel:value=>value,
 authorityStep:(label,observed,detail)=>{fallback.push({label,observed,detail});return 'LEGACY_'+label;},
});
for(const [start,end] of [
 ['const I18N =', 'let currentLanguage'],
 ['function t(', 'function modelSuggestion('],
 ['function escapeHtml(value)','function toast('],
 ['function currentRunId(state)','function exactTaskInvocation'],
 ['function activeCompetition(state)','function stateExecution'],
 ['function renderAuthorityLadder(state)','const DATA_JOURNEY_SLOT_KEYS'],
])vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),context);
const state={schema_version:'orgrebase.workspace-state.v2',execution:{run_id:'run:current'},
 changes:{'change:current':{preview:null,approval:null,outcome:null}},
 change_events:[{event_id:'change:current',event_digest:digest,status:'PREVIEWED'}],
 competition_evidence:{status:'PASS',run_id:'run:current',project_terminal_state:'completed',agentteams_action_count:23},
 execution_activity:{rows:[]}};
function render(value=state){fallback=[];context.renderAuthorityLadder(value);return nodes.get('authority-ladder-steps').innerHTML;}
const phases=html=>[...html.matchAll(/data-phase="([^"]+)"/g)].map(match=>match[1]);
'''


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_ladder_calls_current_change_progress_and_switches_language_and_fallback():
    run_node(PROGRESS_RENDERER + r'''
let html=render();
assert.equal(helperCalls,1);assert.equal(fallback.length,0);
assert.equal(nodes.get('authority-ladder').hidden,false);
assert.equal((html.match(/<article /g)||[]).length,6);
assert.deepEqual([...html.matchAll(/data-step="([^"]+)"/g)].map(match=>match[1]),
 ['proposal','agentteams','candidate','review','human','effect']);
assert.deepEqual(phases(html),['complete','current','waiting','blocked','unobserved','waiting']);
assert(html.includes('CURRENT_CHANGE_agentteams'));
assert(!html.includes('LEGACY_'),'completed formation observations cannot replace the selected change stages');
assert.equal(nodes.get('authority-ladder-title').textContent,'One change, from proposal to committed result');
assert(nodes.get('authority-ladder-context').textContent.includes('change:current'));
assert(html.includes('In progress'));assert(html.includes('Stopped'));assert(html.includes('Not observed'));
context.currentLanguage='zh-CN';html=render();
assert.equal(helperCalls,2);assert.equal(fallback.length,0);
assert.equal(nodes.get('authority-ladder-title').textContent,'同一项变更\uff0c从提出到正式生效');
assert(html.includes('本次变更 candidate'));assert(html.includes('进行中'));assert(html.includes('已停止'));
context.currentLanguage='en';assert(render().includes('Current change candidate'));
progress=null;html=render();
assert.equal(fallback.length,5);assert(!html.includes('CURRENT_CHANGE_'));assert.equal(phases(html).length,0);
assert.equal(nodes.get('authority-ladder-title').textContent,'From team delivery to rules taking effect');
assert.equal(fallback.find(step=>step.label==='authorityLadder.at').observed,true,
 'the separately labelled formation fallback remains available');
selected=null;context.currentLanguage='zh-CN';render({execution:{run_id:'UNAVAILABLE'}});
assert.equal(nodes.get('authority-ladder').hidden,true);
assert.equal(nodes.get('authority-ladder-title').textContent,'从团队交付到规则生效');
assert(!nodes.get('authority-ladder-context').textContent.includes('change:current'));
assert(fallback.every(step=>!step.observed));
''')


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("mismatch", [
    "selected_run", "selected_digest", "state_event_digest", "progress_run", "progress_event",
    "progress_digest", "five_steps", "missing_steps",
])
def test_ladder_rejects_mismatched_or_incomplete_progress(mismatch):
    run_node(PROGRESS_RENDERER + r'''
assert.equal(phases(render()).length,6);
const calls=helperCalls;
if(MISMATCH==='selected_run')selected.execution_run_id='run:other';
if(MISMATCH==='selected_digest')selected.event.digest=otherDigest;
if(MISMATCH==='state_event_digest')state.change_events[0].event_digest=otherDigest;
if(MISMATCH==='progress_run')progress.execution_run_id='run:other';
if(MISMATCH==='progress_event')progress.event_id='change:other';
if(MISMATCH==='progress_digest')progress.event_digest=otherDigest;
if(MISMATCH==='five_steps')progress.steps.pop();
if(MISMATCH==='missing_steps')progress.steps=[];
const html=render();assert.equal(fallback.length,5);assert.equal(phases(html).length,0);
assert(!html.includes('CURRENT_CHANGE_'));
assert.equal(nodes.get('authority-ladder-title').textContent,'From team delivery to rules taking effect');
if(['selected_run','selected_digest','state_event_digest'].includes(MISMATCH))assert.equal(helperCalls,calls,
 'invalid selected identity cannot ask the helper for progress');
else assert.equal(helperCalls,calls+1);
'''.replace("MISMATCH", json.dumps(mismatch)))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_ladder_escapes_progress_text_and_constrains_phase_tokens():
    run_node(PROGRESS_RENDERER + r'''
const attack='"><img src=x onerror="alert(1)"><script>alert(2)</script>&\'';
progress.steps[0].key=attack;progress.steps[0].text=attack;
progress.steps[1].status=attack;
selected.status=attack;
const html=render();assert.equal(fallback.length,0);
assert(!html.includes('<img'));assert(!html.includes('<script'));assert(!html.includes('data-phase="'+attack));
assert(html.includes('&lt;img'));assert(html.includes('&quot;'));assert(html.includes('&amp;'));assert(html.includes('&#039;'));
assert.equal(phases(html)[1],'unobserved','unrecognized status never becomes markup or a completed stage');
assert.equal((html.match(/<article /g)||[]).length,6);
assert.equal(nodes.get('authority-ladder-context').innerHTML,'');
assert(nodes.get('authority-ladder-context').textContent.includes(attack),'context uses textContent, not interpreted HTML');
''')
