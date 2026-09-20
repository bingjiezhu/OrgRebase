"""Exercise shipped amount rendering and structured policy editing in the DOM."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")


def test_current_quote_renders_server_money_without_recalculation_or_html() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,nodes}=require('./tests/workspace/console_dom_harness.js');
const section=document.createElement('section');section.id='quote-pricing';
const source=fs.readFileSync('demo/console/app.js','utf8');
const context=vm.createContext({document,byId:id=>nodes.get(id),DEFAULT_LANGUAGE:'zh-CN',currentLanguage:'en'});
vm.runInContext(source.slice(source.indexOf('const I18N ='),source.indexOf('let currentLanguage'))
 + source.slice(source.indexOf('function t('),source.indexOf('function modelSuggestion(')),context);
vm.runInNewContext(source.slice(source.indexOf('function renderQuotePricing('),source.indexOf('function renderQuote(state)')),context);
const walk=node=>[node,...node.children.flatMap(walk)];const text=()=>walk(section).map(n=>n.textContent||'').join('\n');
const pricing={currency:'GBP',minor_units:2,lines:[{line_id:'one',sku:'SKU',description:'<img src=x onerror=evil()>',quantity:3,unit_price:'0.335',line_total:'1.01'}],
 subtotal:'1.01',discount_rate_bps:125,discount_amount:'0.01',net_amount:'1.00',tax_rate_bps:2000,tax_label:'CONTROLLED DEMO TAX',tax_amount:'0.20',total:'1.20',
 rounding_description:'HALF_UP per line and amount',basket_source_ref:'uci:historical-invoice',policy_source_ref:'controlled:example',basket_digest:'sha256:basket',policy_digest:'sha256:policy'};
const before=JSON.stringify(pricing);context.renderQuotePricing(pricing);
assert.equal(section.hidden,false);assert(text().includes('0.335 GBP'));
assert(text().includes('1.01 GBP'));assert(text().includes('1.20 GBP'));assert(text().includes('1.25%'));
assert(text().includes('CONTROLLED DEMO TAX'));assert(text().includes('uci:historical-invoice'));
assert(text().includes('SKU SKU'));assert(text().includes('Line ID one'));
assert(text().includes('HALF_UP per line and amount'));
assert(text().includes('<img src=x onerror=evil()>'));assert(!walk(section).some(n=>n.tagName==='IMG'));
assert.equal(JSON.stringify(pricing),before,'display does not mutate or recalculate the committed input');
document.documentElement.lang='zh-CN';context.currentLanguage='zh-CN';context.renderQuotePricing(pricing);assert(text().includes('报价总额'));
context.renderQuotePricing(undefined);assert.equal(section.hidden,true);assert.equal(section.children.length,0,'unpriced quote has no invented zero amounts');
''')


def test_pricing_policy_editor_uses_exact_basis_points_and_preserves_bound_fields() -> None:
    source = json.dumps((ROOT / "demo/console/change-workbench.js").read_text())
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require('./tests/workspace/console_dom_harness.js');
Object.getPrototypeOf(document.createElement('input')).removeAttribute=function(key){delete this.attributes[key];};
const policy={discount_bps:0,tax_bps:2000,tax_label:'Controlled demo tax',tax_mode:'EXCLUSIVE',rounding:'HALF_UP',source_ref:'source:old'};
const configuration={execution_run_id:'run:pricing',fields:[{slot_id:'pricing_policy',value_kind:'pricing_policy',current:{version:'v1',digest:'sha256:base',state:'CURRENT',value:policy},owner_id:'owner:finance',allowed_operations:['UPDATE']}]};
const submitted=[],session={mode:'local',principal:null};let uuid=0,registered=null;
window.OrgRebaseClient={session:()=>session,workspace:()=> 'workspace:one',async json(path,request={}){
 if(path.endsWith('/change-options'))return structuredClone(configuration);
 if(path.includes('/changes?'))return {items:registered?[structuredClone(registered)]:[],next_cursor:null};
 if(path.endsWith('/change-proposals')){
  const body=structuredClone(request.body);submitted.push(body);
  registered={execution_run_id:'run:pricing',status:'RECEIVED',allowed_actions:[],event:{event_id:body.event_id,slot_id:body.slot_id,owner_id:'owner:finance',base_version:body.base_version,base_digest:body.base_digest,proposal:{id:'policy:one',version:'proposal-v2',payload:{canonical_value:body.value},source_refs:[body.source_ref]}}};
  return {event_id:body.event_id};
 }
 if(registered&&path.endsWith('/changes/'+encodeURIComponent(registered.event.event_id)))return structuredClone(registered);
 throw new Error(path);
}};
vm.runInNewContext(SOURCE,{window,document,CustomEvent,performance,crypto:{randomUUID:()=>String(++uuid)}});
async function input(id,value){nodes.get(id).value=value;await nodes.get(id).emit('input');}
(async()=>{
 await tick();await tick();
 assert.equal(nodes.get('change-value').value,'0.00');assert.equal(nodes.get('change-tax-rate').value,'20.00');
 await input('change-source','controlled:policy-v2');await input('change-value','1.001');
 await nodes.get('change-proposal-form').emit('submit');assert.equal(submitted.length,0,'too much precision is rejected, not rounded');
 await input('change-value','100.01');await nodes.get('change-proposal-form').emit('submit');assert.equal(submitted.length,0);
 await input('change-value','1e1');await nodes.get('change-proposal-form').emit('submit');assert.equal(submitted.length,0);
 await input('change-value','12.34');await input('change-tax-rate','7.05');
 await window.OrgRebaseChangeWorkbench.refresh();assert.equal(nodes.get('change-value').value,'12.34','refresh preserves typed policy draft');
 await nodes.get('change-proposal-form').emit('submit');await tick();assert.equal(submitted.length,1);
 const body=submitted[0];assert.equal(body.value.discount_bps,1234);assert.equal(body.value.tax_bps,705);
 assert.equal(body.value.tax_mode,'EXCLUSIVE');assert.equal(body.value.rounding,'HALF_UP');
 assert.equal(body.value.tax_label,'Controlled demo tax');assert.equal(body.value.source_ref,'controlled:policy-v2');
 assert.equal(body.base_digest,'sha256:base');assert(!Object.hasOwn(body,'pricing_input'));
 assert.equal(policy.discount_bps,0,'proposing a new rule never changes the current rule');
 configuration.fields[0].current={...configuration.fields[0].current,version:'v2',digest:'sha256:base-v2',value:body.value};
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(nodes.get('change-submit').disabled,false,'untouched prefilled policy draft follows a committed update');
 assert.equal(nodes.get('change-value').value,'12.34');
 assert(!nodes.get('change-notice').textContent.includes('base changed'),'a successful command does not invent an edited stale draft');
 await input('change-value','8.25');
 configuration.fields[0].current={...configuration.fields[0].current,version:'v3',digest:'sha256:base-v3',value:{...body.value,discount_bps:500}};
 await window.OrgRebaseChangeWorkbench.refresh();
 assert.equal(nodes.get('change-value').value,'8.25','actual unsent user edits are preserved across another update');
 assert.equal(nodes.get('change-submit').disabled,true,'actual user edits must be reviewed against the new base');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("SOURCE", source))


def test_amount_comparison_uses_exact_versions_and_identifies_changed_baskets() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window}=require('./tests/workspace/console_dom_harness.js');
const source=fs.readFileSync('demo/console/change-workbench.js','utf8');
const context={document,tr:(zh,en)=>en,matching:(a,b)=>typeof a==='string'&&a.length>0&&a===b};
vm.runInNewContext(source.slice(source.indexOf('  function renderPricingComparison('),source.indexOf('  function renderChangeResult(')),context);
const section=document.createElement('section'),walk=node=>[node,...node.children.flatMap(walk)];
const pricing={currency:'GBP',basket_digest:'sha256:one',subtotal:'100.00',discount_amount:'0.00',net_amount:'100.00',tax_amount:'20.00',total:'120.00',tax_label:'Controlled tax'};
const before={version:'v1',payload:{pricing}},after={version:'v2',payload:{pricing:{...pricing,discount_amount:'10.00',net_amount:'90.00',tax_amount:'18.00',total:'108.00'}}};
context.renderPricingComparison(section,before,after);
const text=()=>walk(section).map(n=>n.textContent||'').join('\n');
const headers=()=>walk(section).find(n=>n.tagName==='THEAD').children[0].children.map(n=>n.textContent);
assert.deepEqual(headers(),['Amount','Before v1','After v2']);
assert(text().includes('Same basket'));assert(text().includes('120.00 GBP'));assert(text().includes('108.00 GBP'));
assert(walk(section).find(n=>n.dataset.pricingField==='total').dataset.result==='CHANGED');
assert(walk(section).find(n=>n.dataset.pricingField==='subtotal').dataset.result==='PRESERVED');
section.replaceChildren();after.payload.pricing.basket_digest='sha256:other';context.renderPricingComparison(section,before,after);
assert(text().includes('basket also changed'));assert(!text().includes('Same basket'));
section.replaceChildren();context.tr=(zh,en)=>zh;context.renderPricingComparison(section,before,after);
assert.deepEqual(headers(),['金额项','变更前 v1','变更后 v2']);
section.replaceChildren();context.renderPricingComparison(section,before,{...after,version:'拟议结果'},{preview:true});
assert.deepEqual(headers(),['金额项','变更前 v1','拟议结果'],'preview must not imply an applied successor');
section.replaceChildren();context.renderPricingComparison(section,{payload:{}},after);assert.equal(section.children.length,0);
''')


def test_pricing_preview_requires_the_exact_proposal_snapshot_and_predecessor() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window}=require('./tests/workspace/console_dom_harness.js');
const source=fs.readFileSync('demo/console/change-workbench.js','utf8');
const pricing={currency:'GBP',basket_digest:'sha256:basket',subtotal:'100.00',discount_amount:'0.00',net_amount:'100.00',tax_amount:'20.00',total:'120.00',tax_label:'Controlled tax',
 lines:[{line_id:'same-line',sku:'OLD-SKU',description:'Original',quantity:2,unit_price:'50.00',line_total:'100.00'}]};
const predecessor={ref:'quote@v1',version:'v1',digest:'sha256:quote-v1',payload:{pricing}};
const detail={status:'PREVIEWED',execution_run_id:'run:one',event:{event_id:'price:one',proposal:{id:'policy:one',version:'proposal-v2',digest:'sha256:proposal'}},preview:{
 kind:'price:one',preview_digest:'sha256:preview',bundle:{snapshot_digest:'sha256:snapshot',change_spec:{object_id:'policy:one',proposed_version:'proposal-v2',expected_snapshot_digest:'sha256:snapshot'},preview:{digest:'sha256:preview'}},
 pricing_comparison:{predecessor_ref:'quote@v1',predecessor_digest:'sha256:quote-v1',proposal_digest:'sha256:proposal',snapshot_digest:'sha256:snapshot',before:pricing,after:{...pricing,total:'108.00',
  lines:[{...pricing.lines[0],sku:'NEW-SKU'}]}}}};
const state={execution:{run_id:'run:one'},enterprise_data_lineage:{run_id:'run:one',quotes:{versions:[predecessor]}}};
const context={document,tr:(zh,en)=>en,matching:(...values)=>typeof values[0]==='string'&&values[0].length>0&&values.every(v=>v===values[0]),
 detail,selectedId:'price:one',options:{execution_run_id:'run:one'},state};
vm.runInNewContext(source.slice(source.indexOf('  function renderPricingComparison('),source.indexOf('  function renderChangeResult(')),context);
const section=document.createElement('section'),walk=node=>[node,...node.children.flatMap(walk)],text=()=>walk(section).map(n=>n.textContent||'').join('\n');
section.querySelector=selector=>walk(section).find(node=>'#'+node.id===selector);
context.renderPricingPreview(section);assert(text().includes('not yet applied'));assert(text().includes('108.00 GBP'));
assert(text().includes('OLD-SKU'));assert(text().includes('NEW-SKU'));
const lineChanges=()=>walk(section).find(node=>node.id==='change-pricing-preview-lines');
assert.equal(lineChanges().open,false);lineChanges().open=true;lineChanges().children[0].focus();
context.renderPricingPreview(section);assert.equal(lineChanges().open,true,'state refresh preserves an expanded exact-proposal comparison');
assert.equal(document.activeElement,lineChanges().children[0],'state refresh preserves keyboard focus on the line summary');
const original=structuredClone(detail);
context.detail=structuredClone(original);
context.detail.preview.pricing_comparison.before=Object.fromEntries(Object.entries(pricing).reverse());
context.detail.preview.pricing_comparison.before.lines=pricing.lines.map(line=>Object.fromEntries(Object.entries(line).reverse()));
context.renderPricingPreview(section);assert(text().includes('108.00 GBP'),'object field order must not hide a valid preview');
assert(context.equalJsonValue({a:1,b:[{x:2,y:3}]},{b:[{y:3,x:2}],a:1}));
assert(!context.equalJsonValue({a:[1,2]},{a:[2,1]}),'array order stays meaningful');
assert(!context.equalJsonValue({a:1},{a:'1'}),'types stay meaningful');
assert(!context.equalJsonValue({amount:'100.00'},{amount:'100.01'}),'amount changes are not reordered fields');
assert(!context.equalJsonValue({a:NaN},{a:NaN}));
for(const mutate of [
 d=>d.preview.pricing_comparison.proposal_digest='sha256:other',
 d=>d.preview.pricing_comparison.snapshot_digest='sha256:other',
 d=>d.preview.pricing_comparison.predecessor_digest='sha256:other',
 d=>d.preview.pricing_comparison.before={...pricing,total:'119.00'},
 d=>d.preview.bundle.preview.digest='sha256:other',
 d=>d.execution_run_id='run:other',
]){
 context.detail=structuredClone(original);mutate(context.detail);context.renderPricingPreview(section);
 assert(!walk(section).some(n=>n.tagName==='TABLE'),'mismatched binding never displays a verified amount preview');
 assert(text().includes('not yet matched'));
}
context.detail={...structuredClone(original),status:'EXPIRED'};context.renderPricingPreview(section);assert(section.hidden);
''')


def test_same_total_line_changes_are_reviewable_without_recalculating_or_rendering_unchanged_lines() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window}=require('./tests/workspace/console_dom_harness.js');
const source=fs.readFileSync('demo/console/change-workbench.js','utf8');
const context={document,tr:(zh,en)=>en,matching:(a,b)=>typeof a==='string'&&a.length>0&&a===b};
vm.runInNewContext(source.slice(source.indexOf('  function renderPricingComparison('),source.indexOf('  function renderChangeResult(')),context);
const section=document.createElement('section'),walk=node=>[node,...node.children.flatMap(walk)];
const text=()=>walk(section).map(n=>n.textContent||'').join('\n');
const line={line_id:'row1',sku:'OLD-SKU',description:'Original item',quantity:3,unit_price:'12.50',line_total:'37.50'};
const pricing={currency:'USD',basket_digest:'sha256:old',subtotal:'37.50',discount_amount:'1.88',net_amount:'35.62',tax_amount:'7.12',total:'42.74',tax_label:'Controlled tax',lines:[line]};
const before={version:'v1',payload:{pricing}};
const after={version:'v2',payload:{pricing:{...pricing,basket_digest:'sha256:new',lines:[{...line,sku:'NEW-SKU',description:'<img src=x onerror=evil()>',quantity:5,unit_price:'7.50'}]}}};
const saved=JSON.stringify([before,after]);
context.renderPricingComparison(section,before,after);
const changes=()=>walk(section).filter(node=>node.dataset.quoteLineChange);
assert.equal(changes().length,1);assert.equal(changes()[0].dataset.quoteLineChange,'MODIFIED');
assert.equal(walk(section).find(node=>node.id==='change-pricing-result-lines').open,false);
assert(text().includes('Applied line changes: Modified 1'));
for(const value of ['OLD-SKU','NEW-SKU','Quantity: 3','Quantity: 5','12.50 USD','7.50 USD','42.74 USD'])assert(text().includes(value),value);
assert(walk(section).filter(node=>node.dataset.pricingField).every(node=>node.dataset.result==='PRESERVED'),'same totals do not hide a line substitution');
assert(text().includes('<img src=x onerror=evil()>'));assert(!walk(section).some(node=>node.tagName==='IMG'));
assert.equal(JSON.stringify([before,after]),saved,'comparison must preserve exact server values');
section.replaceChildren();context.tr=(zh,en)=>zh;
context.renderPricingComparison(section,before,{...after,version:'拟议结果'},{preview:true});
assert(text().includes('拟议商品明细变化：修改 1'));assert(!text().includes('已生效商品明细变化'));
assert.equal(changes()[0].children[0].textContent,'row1 · 修改');
const details=walk(section).find(node=>node.id==='change-pricing-preview-lines');
assert.equal(details.open,false);
assert.equal(walk(details).find(node=>node.tagName==='THEAD').children[0].children[2].textContent,'拟议结果');
// Stable line IDs distinguish removal/addition even when SKU and amount are equal.
after.payload.pricing.lines=[{...line,line_id:'row2'}];section.replaceChildren();context.tr=(zh,en)=>en;
context.renderPricingComparison(section,before,after);
assert.deepEqual(changes().map(node=>node.dataset.quoteLineChange),['REMOVED','ADDED']);
assert(text().includes('No line in this version'));
// A large supported basket shows only changed rows and remains collapsed.
before.payload.pricing.lines=Array.from({length:200},(_,i)=>({...line,line_id:'line-'+i}));
after.payload.pricing.lines=structuredClone(before.payload.pricing.lines);
after.payload.pricing.lines[199].unit_price='12.500000000000000001';
section.replaceChildren();context.renderPricingComparison(section,before,after);
assert.equal(changes().length,1);assert.equal(changes()[0].dataset.lineId,'line-199');
assert(text().includes('12.500000000000000001 USD'),'unit price decimal lexeme is not converted through float');
assert.equal(walk(section).find(node=>node.id==='change-pricing-result-lines').open,false);
after.payload.pricing.lines=structuredClone(before.payload.pricing.lines);
section.replaceChildren();context.renderPricingComparison(section,before,after);
assert.equal(changes().length,0);assert(!walk(section).some(node=>node.tagName==='DETAILS'),'source-only changes do not invent line edits');
''')


def test_business_timestamp_labels_follow_explicit_logical_clock_basis():
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/change-workbench.js','utf8');
const helper=source.slice(source.indexOf('  function usesLogicalBusinessTime('),source.indexOf('  function appliedEvidence('));
const context={state:{execution:{}},tr:(zh,en)=>en};vm.createContext(context);vm.runInContext(helper,context);
assert.equal(context.businessTimeLabel('审批有效期','Approval validity'),'Approval validity');
context.state.execution.oac_agentteams_lineage={context_freshness_basis:'LOGICAL_EVENT_TIME'};
assert.match(context.businessTimeLabel('审批有效期','Approval validity'),/business logical time/);
context.state={enterprise_data_lineage:{oac:{context_freshness_basis:'LOGICAL_EVENT_TIME'}}};
assert.equal(context.usesLogicalBusinessTime(),true);
context.state={execution:{oac_agentteams_lineage:{context_freshness_basis:'SYSTEM_CLOCK'}}};
assert.equal(context.usesLogicalBusinessTime(),false);
context.state={};assert.equal(context.usesLogicalBusinessTime(),false);
''')
