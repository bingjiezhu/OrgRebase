"""Expose matched machine-review evidence without promoting it to human authority."""
from __future__ import annotations

import json
import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")

HARNESS = r'''
const assert=require('node:assert/strict'), vm=require('node:vm');
const source=fs.readFileSync('demo/console/app.js','utf8');
const state=JSON.parse(fs.readFileSync('evidence/golden-competition/latest/pilot/state.json','utf8'));
const collaboration=state.competition_evidence.agent_collaboration;
const tasks=collaboration.orchestration_plan.tasks.filter(task=>task.role==='REVIEWER');
const runs=collaboration.agent_runs.filter(run=>run.role==='REVIEWER');
const context=vm.createContext({displayToken:value=>value,localizedFactHtml:value=>value});
for(const [start,end] of [
 ['const I18N =','function modelSuggestion('],
 ['function isSha256Digest(','function toast('],
 ['function agentWorkTaskReceipts(','function agentWorkCapabilityEntries('],
]) vm.runInContext((start==='const I18N ='?'const DEFAULT_LANGUAGE="zh-CN";':'')+source.slice(source.indexOf(start),source.indexOf(end)),context);
function render(taskRows=tasks,runRows=runs,language='zh-CN') {
 vm.runInContext(`currentLanguage=${JSON.stringify(language)}`,context);
 return context.agentWorkTaskReceipts(taskRows,runRows);
}
'''


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_reviewer_shows_actual_per_task_advice_disposition_and_process_boundary(language: str) -> None:
    run_node(HARNESS + "const language=" + json.dumps(language) + r''';
assert.equal(tasks.length,2);
assert.equal(runs[0].model_advisory_accepted,false);
assert.equal(runs[1].model_advisory_accepted,true);
const html=render(tasks,runs,language);
for(const task of tasks) assert(html.includes(task.id));
assert.match(html,language==='en'?/Independent executor process recorded/:/已记录独立执行进程/);
assert.match(html,language==='en'?/Model advice not accepted/:/模型建议未采纳/);
assert.match(html,language==='en'?/Model advice accepted/:/模型建议已采纳/);
assert.match(html,language==='en'?/Human approval is recorded separately in change approval/:/人工批准在变更审批中单独记录/);
assert(!html.includes('provider_request_id') && !html.includes('model_claim_boundary'));
assert.match(render(tasks,runs,'en'),/Model advice not accepted/);
assert.match(render(tasks,runs,'zh-CN'),/模型建议未采纳/);
''')


@pytest.mark.parametrize("field,value", [
    ("task_id", "other-task"),
    ("input_digest", "sha256:" + "f" * 64),
    ("output_digest", "sha256:" + "f" * 64),
    ("role", "DOMAIN_WORKER"),
    ("agent_name", "other-reviewer"),
    ("attempt", 99),
])
def test_reviewer_ignores_other_tasks_or_unbound_process_facts(field: str, value: object) -> None:
    run_node(HARNESS + "const mutation=" + json.dumps({field: value}) + r''';
const changed=structuredClone(runs[0]);Object.assign(changed,mutation);
const html=render([tasks[0]],[changed]);
assert(!html.includes('agent-work-review-process'));
assert(html.includes(tasks[0].id),'the task remains inspectable without invented review evidence');
''')


def test_reviewer_does_not_coerce_unknown_values_or_disclose_unrelated_raw_fields() -> None:
    run_node(HARNESS + r'''
const run=structuredClone(runs[0]);
run.independent_process='true';run.model_advisory_accepted='false';
run.raw_response='PRIVATE_RAW_RESPONSE';
assert(render([tasks[0]],[run]).includes('未记录\uFF0C无法判断'));
assert(!render([tasks[0]],[run]).includes('模型建议未采纳'));
assert(!render([tasks[0]],[run]).includes('PRIVATE_RAW_RESPONSE'));
const worker=collaboration.orchestration_plan.tasks.find(task=>task.role==='DOMAIN_WORKER');
const workerRun=collaboration.agent_runs.find(run=>run.task_id===worker.id);
assert(!render([worker],[{...workerRun,model_advisory_accepted:true}]).includes('agent-work-review-process'));
const task={...tasks[0],id:'<img src=x onerror=alert(1)>'};
const unsafe={...runs[0],task_id:task.id};
const html=render([task],[unsafe]);
assert(!html.includes('<img'));
assert(html.includes('&lt;img'));
assert(html.includes('模型建议未采纳'));
''')


def test_reviewer_exposes_decision_disagreement_without_inferring_missing_evidence() -> None:
    run_node(HARNESS + r'''
const run=structuredClone(runs[0]);
run.model_advisory={verdict:'PASS',missing_domains:[],reason_codes:['PRIVATE_MODEL_TEXT']};
run.deterministic_review={verdict:'REPLAN',missing_domains:['legal','finance']};
const html=render([tasks[0]],[run]);
assert(html.includes('PASS · 缺口领域\uFF1A无'));
assert(html.includes('REPLAN · 缺口领域\uFF1A法务, 财务'));
assert(html.includes('确定性复核决定候选是否可用'));
assert(!html.includes('PRIVATE_MODEL_TEXT'));
delete run.model_advisory;
assert(render([tasks[0]],[run]).includes('未记录\uFF0C无法判断'));
run.model_advisory={verdict:'PASS',missing_domains:['<script>private</script>']};
assert(!render([tasks[0]],[run]).includes('private'));
assert(render([tasks[0]],[run]).includes('未记录\uFF0C无法判断'));
''')


def test_review_projection_omits_model_text_and_preserves_unknown() -> None:
    from orgrebase.workspace.service import WorkspaceService

    project = WorkspaceService._golden_review_decision
    assert project({"verdict": "REPLAN", "missing_domains": ["legal", "legal"],
                    "reason_codes": ["PRIVATE_TEXT"]}) == {
        "verdict": "REPLAN", "missing_domains": ["legal"],
    }
    for value in (None, {}, {"verdict": "PASS"},
                  {"verdict": "PASS", "missing_domains": ["private"]},
                  {"verdict": "PASS", "missing_domains": "finance"},
                  {"verdict": "UNKNOWN", "missing_domains": []}):
        assert project(value) is None
