"""Structured source values stay readable without becoming executable markup."""
from __future__ import annotations

import json
import shutil

import pytest

from orgrebase.workspace.pricing import QuoteBasket, QuoteLineInput
from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")


def test_onboarding_and_lineage_share_structured_value_summaries_and_safe_text() -> None:
    basket = QuoteBasket(
        currency="GBP", source_ref="controlled:ui-contract-test",
        items=(
            QuoteLineInput(line_id="one", sku="SKU-1", description="First item", quantity=2, unit_price="4.95"),
            QuoteLineInput(line_id="two", sku="SKU-2", description="Second item", quantity=3, unit_price="1.10"),
        ),
    ).model_dump(mode="json")
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const app=fs.readFileSync('demo/console/app.js','utf8'),shell=fs.readFileSync('demo/console/workspace-shell.js','utf8');
const nodes=new Map();function node(){return {dataset:{},textContent:'',removeAttribute(){}};}
const root=node();root.querySelectorAll=()=>root.values||[];
Object.defineProperty(root,'innerHTML',{set(value){this.html=value;this.values=[...value.matchAll(/data-material-value="(\d+)"/g)].map(m=>({dataset:{materialValue:m[1]},textContent:''}));},get(){return this.html;}});
nodes.set('shell-material-summary',root);nodes.set('shell-material-status',node());const maturity=node();
const document={getElementById:id=>nodes.get(id),querySelector:()=>maturity};
const context=vm.createContext({document,window:{},language:'zh-CN',DEFAULT_LANGUAGE:'zh-CN',currentLanguage:'zh-CN',stateAvailability:'available',displayToken:v=>String(v)});
vm.runInContext(app.slice(app.indexOf('const I18N ='),app.indexOf('let currentLanguage'))
 + app.slice(app.indexOf('function t('),app.indexOf('function modelSuggestion(')),context);
vm.runInContext(app.slice(app.indexOf('function structuredBusinessValue('),app.indexOf('function dataJourneyQuoteReference(')),context);
context.window.structuredBusinessValue=context.structuredBusinessValue;
vm.runInContext(shell.slice(shell.indexOf('  const COPY ='),shell.indexOf('  let language ='))+'\nfunction copy(){return COPY[language];}',context);
context.COMPONENTS=['DOMAIN','KNOWLEDGE','AUTHORITY','CAPABILITY','DEPENDENCY'];
vm.runInContext(shell.slice(shell.indexOf('  function renderMaterials('),shell.indexOf('  function taskScope(')),context);
const policy={discount_bps:1234,tax_bps:705,tax_label:'Controlled <img src=x onerror=evil()> tax',tax_mode:'EXCLUSIVE'};
const basket=__BASKET__;
const other={nested:{note:'<script>evil()</script>'},enabled:false};
const state={scenario:{synthetic:true},enterprise_data_lineage:{source:{status:'ADMITTED_AND_MATCHED',
 components:context.COMPONENTS.map(kind=>({kind,completeness:'COMPLETE',source_root_refs:['source:one'],declared_digest:'sha256:'+'a'.repeat(64)})),
 values:[{slot_id:'pricing_policy',value:policy},{slot_id:'quote_basket',value:basket},{slot_id:'other',value:other}]}}};
const frozen=JSON.stringify(state);context.renderMaterials(state);
assert(root.html.includes('报价计算规则'));assert(root.html.includes('报价商品明细'));
assert.equal(root.values[0].textContent,'整单折扣 12.34% · 税率 7.05% · Controlled <img src=x onerror=evil()> tax');
assert.equal(root.values[1].textContent,'2 项商品明细 · GBP');
assert.equal(root.values[2].textContent,JSON.stringify(other));
assert(!root.html.includes('<img'));assert(!root.html.includes('<script>'));assert(!root.html.includes('[object Object]'));
assert.equal(context.dataJourneyObserved(policy,'pricing_policy'),root.values[0].textContent);
assert.equal(context.dataJourneyObserved(basket,'quote_basket'),root.values[1].textContent);
assert.equal(context.dataJourneyObserved(other),JSON.stringify(other));
context.language='en';context.currentLanguage='en';context.renderMaterials(state);
assert(root.html.includes('Pricing policy'));assert(root.html.includes('Quote line items'));
assert(root.values[0].textContent.startsWith('Order discount 12.34% · Tax rate 7.05%'));
assert.equal(root.values[1].textContent,'2 line items · GBP');assert.equal(JSON.stringify(state),frozen);
assert.equal(context.structuredBusinessValue(true,'notice_required','en'),null);
assert.equal(context.structuredBusinessValue({discount_bps:NaN,tax_bps:20001},'pricing_policy','en'),'Order discount — · Tax rate — · —');
assert(context.structuredBusinessValue({note:'x'.repeat(1000)},'other','en').length<=401);
assert(app.includes('pricing_policy: "dataJourney.slot.pricingPolicy"'));
assert(app.includes('quote_basket: "dataJourney.slot.quoteBasket"'));
const workbench=fs.readFileSync('demo/console/change-workbench.js','utf8');
context.tr=(zh,en)=>en;context.valueText=value=>typeof value==='string'?value:JSON.stringify(value);
vm.runInContext(workbench.slice(workbench.indexOf('  function basisPointsText('),workbench.indexOf('  function percentBasisPoints(')),context);
assert.equal(context.businessValue('quote_basket',basket),'2 basket lines · GBP');
assert(!Object.hasOwn(basket,'lines'),'the input contract uses items; lines belongs only to calculated output');
'''.replace("__BASKET__", json.dumps(basket)))
