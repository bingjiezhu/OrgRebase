"""Pricing does not promote the origin classification of a synthetic organization."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_pricing_badge_preserves_data_origin_boundaries():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('demo/console/app.js','utf8');
const start=source.indexOf('  const dataClass = profile.data_class');
const logic=source.slice(start,source.indexOf('  renderTimeline(normalized);',start));
const helper=source.slice(source.indexOf('function admittedPricingInputs('),source.indexOf('function renderQuotePricing('));
const pricing={lines:[{sku:'test'}],currency:'GBP',total:'10.00',basket_source_ref:'source:uci-online-retail:unverified'};
function label(synthetic,dataClass,value){let label=null;const context={scenario:{synthetic},profile:{data_class:dataClass},normalized:{},quote:{payload:{pricing:value}},t:key=>key,text:(_,v)=>label=v,localizedText:(_,v)=>label=v,byId:()=>({removeAttribute(){}})};vm.runInNewContext(helper+logic,context);return label;}
assert.equal(label(true,'SYNTHETIC_FIXTURE',pricing),'header.syntheticPricedData');
assert.equal(label(false,'SYNTHETIC_FIXTURE',pricing),'header.syntheticPricedData');
assert.equal(label(true,'SYNTHETIC_FIXTURE',null),'header.syntheticData');
assert.equal(label(true,'SYNTHETIC_FIXTURE',{}),'header.syntheticData');
assert.equal(label(true,'SYNTHETIC_FIXTURE',{...pricing,lines:[]}),'header.syntheticData');
assert.equal(label(false,'CUSTOMER_DECLARED',pricing),'CUSTOMER_DECLARED');
assert.equal(label(false,null,pricing),'header.declaredData');
assert(source.includes('受控企业样例 · 计价输入来源见报价'));
assert(source.includes('CONTROLLED ENTERPRISE SAMPLE · PRICING INPUT SOURCES IN QUOTE'));
'''
    result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT,
                            text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_admitted_input_summary_uses_policy_input_schema_and_never_infers_provenance():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('demo/console/app.js','utf8');
const helper=source.slice(source.indexOf('function admittedPricingInputs('),source.indexOf('function renderQuotePricing('));
const context={};vm.createContext(context);vm.runInContext(helper,context);
const state={enterprise_data_lineage:{source:{status:'ADMITTED_AND_MATCHED',values:[
 {slot_id:'quote_basket',source_ref:'source:declared-basket@r1',value:{currency:'GBP',items:[{sku:'one'}]}},
 {slot_id:'pricing_policy',source_ref:'source:controlled-policy@r1',value:{discount_bps:1000,tax_bps:2000,tax_mode:'EXCLUSIVE'}}]}}};
const result=context.admittedPricingInputs(state);assert.equal(result.discountBps,1000);assert.equal(result.taxBps,2000);
assert.equal(result.basketSource,'source:declared-basket@r1');assert(!Object.hasOwn(result,'real_data_verified'));
for(const mutate of [s=>s.enterprise_data_lineage.source.status='UNAVAILABLE',s=>s.enterprise_data_lineage.source.values.push(s.enterprise_data_lineage.source.values[0]),s=>s.enterprise_data_lineage.source.values[1].value.tax_bps='UNKNOWN',s=>s.enterprise_data_lineage.source.values[1].value.discount_bps=10001]){
 const invalid=structuredClone(state);mutate(invalid);assert.equal(context.admittedPricingInputs(invalid),null);
}
'''
    result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT,
                            text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
