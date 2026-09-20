"""Archive recheck facts remain separate from business completion and stale data."""
import json

from tests.workspace.test_workspace_client import CONSOLE, run_node


def test_recheck_cards_show_exact_scope_and_clear_stale_or_invalid_numbers():
    source = (CONSOLE / "archive-revalidation.js").read_text()
    run_node(r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const {document,window}=require('./tests/workspace/console_dom_harness.js');
const root=document.createElement('section');root.id='archive-revalidation';
const context={document,window};vm.runInNewContext(__SOURCE__,context);
const render=v=>window.OrgRebaseArchiveRevalidation.render(v,'en');
const walk=n=>[n,...n.children.flatMap(walk)],text=()=>walk(root).map(n=>n.textContent||'').join('\n');
const common={status:'PASS',original_archive:'evidence/original/latest',reason_codes:[],artifacts:[{path:'evidence/recheck/report.json',sha256:'a'.repeat(64)}],execution_run_id:null};
const view={schema_version:'orgrebase.archive-revalidation-view.v1',status:'PASS',current_business_run:false,live_model_calls:0,manifest_digest:'sha256:'+'b'.repeat(64),checked_at:'2026-09-18T03:00:00Z',items:[
 {...common,id:'bpi',mode:'INDEPENDENT_RECOMPUTATION',summary:{queries:128,strategies:3}},
 {...common,id:'formation',mode:'NEW_DETERMINISTIC_TASKFLOW',execution_run_id:'run:new:formation',summary:{domains:['legal','product'],task_bindings:3,agentteams_actions:20,canonical_target_writes:0}},
 {...common,id:'skill',mode:'EXACT_PREDECESSOR_REPLAY',execution_run_id:'run:original:skill',summary:{negative_probes:4,restoration_status:'EXECUTED_AND_INVOKED',canonical_target_writes:0,process_restart_proven:false}},
 {...common,id:'owb',mode:'REFERENCE_SUT_REEXECUTION',summary:{profiles:13,cases_per_profile:192,reference_score:100,failed_strategy_profiles:['no-certificate']}}]};
render(view);assert(text().includes('4/4 checks usable'));assert(text().includes('128 queries'));assert(text().includes('Original run identity retained by replay'));assert(text().includes('run:original:skill'));
const stale=structuredClone(view);stale.status='PARTIAL';stale.items[0].status='STALE';render(stale);assert(!text().includes('128 queries'));assert(text().includes('3/4 checks usable'));
const corrupt=structuredClone(view);corrupt.items[0].summary.queries='UNKNOWN';render(corrupt);assert(!text().includes('UNKNOWN'));assert(!text().includes('128 queries'));
render({...view,current_business_run:true});assert(!text().includes('13 profiles'));assert(text().includes('no usable revalidation report'));
render({...view,live_model_calls:1});assert(!text().includes('128 queries'));
render({...view,checked_at:'not-a-date'});assert(!text().includes('128 queries'));
window.OrgRebaseArchiveRevalidation.render(view,'zh-CN');assert(text().includes('原始档案'));assert(text().includes('重放保留的原运行标识'));
render(null);assert(!text().includes('128'));assert(!text().includes('run:original:skill'));
'''.replace("__SOURCE__", json.dumps(source)))


def test_validation_directory_distinguishes_sources_and_navigates_without_changing_route():
    source = (CONSOLE / "archive-revalidation.js").read_text()
    run_node(r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const {document,window}=require('./tests/workspace/console_dom_harness.js');
const root=document.createElement('section');root.id='archive-revalidation';
const navigation=document.createElement('nav');navigation.id='validation-directory';
for(let i=0;i<3;i++){
 const entry=document.createElement('a');
 for(const tag of ['strong','span','small'])entry.append(document.createElement(tag));
 navigation.append(entry);
}
const publicCase=document.createElement('details');publicCase.id='public-real-process-validation';
let scrolls=0,prevented=0;publicCase.scrollIntoView=()=>scrolls++;
vm.runInNewContext(__SOURCE__,{document,window});
window.dispatchEvent({type:'orgrebase:languagechange',detail:{language:'en'}});
assert.equal(navigation.children[0].children[0].textContent,'Current task records');
window.dispatchEvent({type:'orgrebase:languagechange',detail:{language:'zh'}});
assert.equal(navigation.children[0].children[0].textContent,'本次任务记录');
navigation.children[1].onclick({preventDefault(){prevented++}});
assert.equal(prevented,1);assert.equal(scrolls,1);assert.equal(publicCase.open,true);
prevented=0;scrolls=0;publicCase.open=false;
const render=lang=>window.OrgRebaseArchiveRevalidation.render(null,lang);
render('en');
assert(navigation.children[0].children[0].textContent==='Current task records');
assert(navigation.children[1].children[1].textContent.includes('procurement-rule change'));
assert(navigation.children[1].children[2].textContent.includes('mechanism case'));
assert(navigation.children[2].children[2].textContent.includes('Independent validation runs'));
navigation.children[1].onclick({preventDefault(){prevented++}});
assert.equal(prevented,1);assert.equal(scrolls,1);assert.equal(publicCase.open,true);
render('zh-CN');assert.equal(navigation.children[1].children[1].textContent,'采购规则变化会影响哪些订单？');
assert(navigation.children[2].children[2].textContent.includes('保留失败'));
'''.replace("__SOURCE__", json.dumps(source)))
