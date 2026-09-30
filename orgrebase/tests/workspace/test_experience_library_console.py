"""Read-only capability-center slice for ordinary experience and Finance guidance."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "demo" / "console"
HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {document,window,nodes,tick,CustomEvent} = require('./tests/workspace/console_dom_harness.js');
for (const id of ['experience-library-mount','experience-library-title','experience-library-boundary']) {
  const node=document.createElement('div'); node.id=id;
}
let session={mode:'oidc',authenticated:true,authentication_required:true,
  principal:{issuer:'https://issuer.example',subject:'one',tenant_id:'tenant:one',actor_id:'reader:one',roles:['reader']},csrf_token:'one'};
let workspace='workspace:one';
let responder;
const calls=[];
const failure=code=>Object.assign(new Error(code),{code});
window.OrgRebaseClient={session:()=>session,workspace:()=>workspace,
  refreshSession:async()=>session,refreshWorkspaces:async()=>({}),
  json:async path=>{calls.push(path);return responder(path);}};
const digest='sha256:'+'a'.repeat(64);
const observed={schema_version:'orgrebase.experience-case-page.v1',next_cursor:null,items:[{
  case_ref:'experience-case:one',case_digest:digest,profile_id:'workspace-quote-evidence-recovery-v1',
  observation_status:'OBSERVED',execution_outcome:'APPLIED',assessment:'UNASSESSED',
  assessment_profile_id:'workspace-change-explanation-v1',
  assessment_coverage:'COMPLETE',assessment_count_observed:0}]};
const lessonPage={schema_version:'orgrebase.experience-lesson-page.v1',next_cursor:null,items:[{
  head_ref:'lesson:one',head_digest:digest,state:'CURRENT',status:'QUALIFIED_FOR_RECALL',
  problem_code:'FINANCE_SOURCE_REVIEW',support_cluster_count:2,content_bytes_disclosed:0}]};
const head={profile_id:'workspace-change-explanation-v1',head_ref:'skill-head:one',head_digest:digest,
  package_digest:digest,generation:0,qualification_status:'UNQUALIFIED',adoption_enabled:false};
const evaluation={status:'PROTOCOL_VALID',quality_status:'NOT_EVALUATED',event_id:'event:one',target_writes:0,
  current_qualification:'CURRENT_INPUTS',current_reason_code:null};
const standard=path=>path.includes('experience-cases')?observed:
  path.includes('experience-lessons')?lessonPage:path.endsWith('/head')?head:evaluation;
const boot=()=>vm.runInNewContext(fs.readFileSync('demo/console/experience-library.js','utf8'),
  {window,document,CustomEvent,Promise});
const settle=async()=>{for(let i=0;i<6;i++)await tick();};
const text=node=>[node?.textContent||'',...(node?.children||[]).map(text)].join(' ');
const visible=()=>text(nodes.get('experience-library-mount'));
const emit=(type,detail)=>window.dispatchEvent(new CustomEvent(type,{detail}));
"""


def run(script: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required for console behavior tests"
    result = subprocess.run(
        [node, "-e", HARNESS + "\n(async()=>{\n" + script +
         "\n})().catch(error=>{console.error(error);process.exitCode=1;});"],
        cwd=ROOT, text=True, capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_current_observation_protocol_and_genesis_never_claim_quality_or_adoption() -> None:
    run(r"""
responder=standard; boot(); await settle();
emit('orgrebase:staterendered',{active_event_id:'event:one'}); await settle();
assert.match(visible(),/1 observed/);
assert.match(visible(),/Eligible for recall · use unobserved/);
assert.match(visible(),/FINANCE_SOURCE_REVIEW/);
assert.match(visible(),/Static baseline · generation 0/);
assert.match(visible(),/Protocol check complete/);
assert.match(visible(),/Quality not evaluated/);
assert.match(visible(),/NOT_RUN · use unobserved/);
assert.doesNotMatch(visible(),/Released · generation/);
assert(calls.every(path=>path.startsWith('/api/workspace/')));
assert.equal(calls.filter(path=>path.includes('/evaluations/')).length,1);
assert.equal(nodes.get('experience-library-mount').getAttribute('aria-busy'),'false');
document.documentElement.lang='zh-CN'; emit('orgrebase:languagechange');
assert.match(visible(),/业务结果不等于学习成功/);
assert.match(visible(),/可召回 · 使用未观测/);
assert.match(visible(),/质量未评/);
assert.match(visible(),/使用未观测/);
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_historical_protocol_pass_does_not_mask_current_source_or_head_hold() -> None:
    run(r"""
responder=path=>path.includes('experience-cases')?observed:
  path.includes('experience-lessons')?lessonPage:path.endsWith('/head')?head:
  {...evaluation,current_qualification:'HOLD',current_reason_code:'FINANCE_EVALUATION_CURRENT_INPUT_STALE'};
boot();await settle();
emit('orgrebase:staterendered',{active_event_id:'event:one'});await settle();
assert.match(visible(),/Historical protocol pass · current inputs stale/);
assert.doesNotMatch(visible(),/Protocol check complete/);
assert.doesNotMatch(visible(),/improved|actual use confirmed/i);
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_session_and_workspace_switch_clear_records_and_ignore_old_responses() -> None:
    run(r"""
let resolveOld;
const pending=new Promise(resolve=>{resolveOld=resolve});
responder=path=>path.includes('experience-cases')?pending:
  path.includes('experience-lessons')?lessonPage:head;
boot();await settle();
assert.match(visible(),/Reading records/);
workspace='workspace:two';
session={...session,principal:{...session.principal,subject:'two',actor_id:'reader:two'},csrf_token:'two'};
responder=path=>path.includes('experience-cases')?{
  ...observed,items:[{...observed.items[0],case_ref:'experience-case:two'}]}:
  path.includes('experience-lessons')?{...lessonPage,items:[{...lessonPage.items[0],head_ref:'lesson:two'}]}:head;
emit('orgrebase:sessionchange',session);
assert.doesNotMatch(visible(),/experience-case:one/);
await settle();
assert.match(visible(),/experience-case:two/);
assert.match(visible(),/lesson:two/);
resolveOld(observed);await settle();
assert.doesNotMatch(visible(),/experience-case:one/);
assert.doesNotMatch(visible(),/lesson:one/);
emit('orgrebase:sessionended');
assert.doesNotMatch(visible(),/experience-case:two/);
assert.equal(nodes.get('experience-library-mount').getAttribute('aria-busy'),'false');
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_old_evaluation_cannot_follow_new_event_and_partial_reads_do_not_claim_success() -> None:
    run(r"""
let resolveOld;
const late=new Promise(resolve=>{resolveOld=resolve});
responder=path=>path.includes('experience-cases')?observed:
  path.includes('experience-lessons')?lessonPage:path.endsWith('/head')?head:
  path.endsWith('/event%3Aone')?late:{status:'NOT_STARTED',event_id:'event:two',target_writes:0};
boot();await settle();
emit('orgrebase:staterendered',{active_event_id:'event:one'});await settle();
emit('orgrebase:staterendered',{active_event_id:'event:two'});await settle();
resolveOld(evaluation);await settle();
assert.match(visible(),/event:two: NOT_STARTED/);
assert.doesNotMatch(visible(),/Protocol check complete/);
responder=path=>path.includes('experience-cases')?observed:
  path.includes('experience-lessons')?Promise.reject(failure('EXPERIENCE_PHASE_AUTHORITY_UNCONFIGURED')):
  path.endsWith('/head')?
  Promise.reject(failure('FINANCE_SKILL_HEAD_NOT_BOOTSTRAPPED')):
  Promise.reject(failure('AUTH_ROLE_DENIED'));
await nodes.get('experience-library-refresh').emit('click');await settle();
assert.match(visible(),/NOT_RUN · no baseline/);
assert.match(visible(),/Read unconfirmed/);
assert.match(visible(),/Lesson entries are unavailable/);
assert.doesNotMatch(visible(),/Released · generation/);
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_qualified_head_and_enabled_selection_still_require_an_actual_use_receipt() -> None:
    run(r"""
responder=path=>path.endsWith('/head')?{
  ...head,generation:1,qualification_status:'QUALIFIED',adoption_enabled:true}:standard(path);
boot();await settle();
assert.match(visible(),/Released · generation 1/);
assert.match(visible(),/Selection enabled · use unobserved/);
assert.doesNotMatch(visible(),/improved|actual use confirmed/i);
assert(calls.every(path=>!path.includes('/bootstrap')&&!path.includes('/decisions')));
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_case_and_lesson_details_show_backend_hold_without_private_text() -> None:
    run(r"""
const caseRef='experience-case:one';
const lesson={...lessonPage,items:[{...lessonPage.items[0],lesson_id:'source-check',
  status:'HOLD',reason_code:'PRIVATE_EPISODE_DELETED',maintenance_status:'ADMITTED'}]};
const cases={...observed,items:[{...observed.items[0],source_state:'HOLD',
  source_reason_code:'PRIVATE_EPISODE_DELETED'}]};
responder=path=>path.includes('/experience-cases/')?{
  schema_version:'orgrebase.experience-case-detail.v1',case_ref:caseRef,case_digest:digest,
  source_state:'HOLD',source_reason_code:'PRIVATE_EPISODE_DELETED',assessment_status:'HOLD',
  assessment_profile_id:'workspace-change-explanation-v1',private_content_disclosed:false,
  actual_use_status:'NOT_CHECKED',private_episode_ref:'SECRET_PRIVATE_REF'}:
  path.includes('/experience-lessons/heads/')?{
    schema_version:'orgrebase.experience-lesson-detail.v1',head_ref:'lesson:one',
    head_digest:digest,lesson_id:'source-check',status:'HOLD',reason_code:'PRIVATE_EPISODE_DELETED',
    maintenance_status:'ADMITTED',related_case_refs:[caseRef],content_bytes_disclosed:0,
    related_case_coverage:'COMPLETE',retrieval_status:'RECALLED',
    actual_use_status:'NOT_CHECKED',body:'SECRET_PRIVATE_TEXT'}:
  path.includes('experience-cases')?cases:path.includes('experience-lessons')?lesson:
  path.endsWith('/head')?head:evaluation;
boot();await settle();
assert.match(visible(),/PRIVATE_EPISODE_DELETED/);
const buttons=()=>nodes.get('experience-library-mount').querySelectorAll('button');
await buttons().find(node=>node.textContent==='View case detail').emit('click');await settle();
assert.match(visible(),/Case detail/);
assert.match(visible(),/Source: HOLD · PRIVATE_EPISODE_DELETED/);
await buttons().find(node=>node.textContent==='View lesson detail').emit('click');await settle();
assert.match(visible(),/Lesson detail/);
assert.match(visible(),/Related cases: experience-case:one/);
assert.match(visible(),/Historical recall: RECALLED · Actual use: NOT_CHECKED/);
assert.doesNotMatch(visible(),/SECRET_PRIVATE_REF|SECRET_PRIVATE_TEXT/);
assert(calls.some(path=>path.includes('/experience-cases/experience-case%3Aone')));
assert(calls.some(path=>path.includes('/experience-lessons/heads/source-check')));
emit('orgrebase:sessionended');
assert.doesNotMatch(visible(),/Lesson detail/);
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_governor_candidate_metadata_is_distinct_from_private_body_and_reader_cannot_fetch() -> None:
    run(r"""
responder=standard;boot();await settle();
assert(calls.every(path=>!path.includes('/experience-lessons/candidates')));
assert.match(visible(),/This identity does not read governed candidates/);
session={...session,principal:{...session.principal,actor_id:'reviewer:one',roles:['governor']}};
responder=path=>path.includes('/experience-lessons/candidates')?{
  schema_version:'orgrebase.experience-candidate-page.v1',items:[{
    candidate_ref:'candidate:one',profile_id:'workspace-change-explanation-v1',
    lesson_id:'source-check',evidence_status:'HOLD',private_body_status:'AVAILABLE',
    review_status:'HOLD',publication_status:'NOT_CURRENT',content_bytes_disclosed:0,
    body:'SECRET_CANDIDATE_BODY'}]}:
  standard(path);
emit('orgrebase:sessionchange',session);await settle();
assert(calls.some(path=>path.includes('/experience-lessons/candidates')));
assert.match(visible(),/candidate:one/);
assert.match(visible(),/HOLD · HOLD · NOT_CURRENT · AVAILABLE/);
assert.doesNotMatch(visible(),/SECRET_CANDIDATE_BODY/);
""")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_late_case_detail_cannot_cross_workspace_or_identity_switch() -> None:
    run(r"""
let release;
const late=new Promise(resolve=>{release=resolve});
responder=path=>path.includes('/experience-cases/')?late:standard(path);
boot();await settle();
const open=nodes.get('experience-library-mount').querySelectorAll('button')
  .find(node=>node.textContent==='View case detail');
open.emit('click');await settle();
assert.match(visible(),/Reading case detail/);
workspace='workspace:two';
session={...session,principal:{...session.principal,actor_id:'reader:two',subject:'two'}};
responder=standard;
emit('orgrebase:sessionchange',session);await settle();
release({schema_version:'orgrebase.experience-case-detail.v1',case_ref:'experience-case:one',
  case_digest:digest,source_state:'HOLD',source_reason_code:'OLD_DETAIL',
  assessment_status:'HOLD',assessment_profile_id:'workspace-change-explanation-v1',
  private_content_disclosed:false,actual_use_status:'NOT_CHECKED'});
await settle();
assert.doesNotMatch(visible(),/OLD_DETAIL|Case detail/);
""")


def test_module_is_shipped_inside_existing_capability_center_without_html_injection() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    js = (CONSOLE / "experience-library.js").read_text(encoding="utf-8")
    css = (CONSOLE / "styles.css").read_text(encoding="utf-8")
    assert 'id="cockpit-skills"' in html
    assert html.index('id="cockpit-skills"') < html.index('id="experience-library-mount"')
    assert '/assets/experience-library.js?v=0.1.0-1' in html
    assert '.experience-library-stages' in css
    assert 'innerHTML' not in js
    assert 'method: "POST"' not in js
