"""Upstream source inspection stays independent of proposal authority and drafts."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_options, submit_change
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_priced_quote_pack import priced_draft
from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("priced", [False, True])
@pytest.mark.parametrize("reader", [False, True])
def test_source_options_expose_binding_domains_without_changing_state_or_authority(tmp_path, priced, reader):
    if priced:
        pack = tmp_path / "priced"
        seal_enterprise_quote_pilot_pack(priced_draft(tmp_path), pack)
    else:
        pack = make_enterprise_pack(tmp_path)
    service = WorkspaceService(store_path=tmp_path / "workspace.sqlite",
                               runtime_configuration=load_enterprise_quote_pilot_pack(pack),
                               review_duration_seconds=0)
    token = None
    try:
        service.form_quote()
        before = (service.current_quote(), service.current_snapshot(), service.store.event_records())
        bindings = {item.slot_id: item for item in service.enterprise_binding.resources
                    if item.slot_id in service.domain_pack.mutable_slots}
        sources = {slot: service.store.get_object(binding.object_id) for slot, binding in bindings.items()}
        if reader:
            token = request_principal.set(Principal(
                "https://identity.example", "reader", service.profile.organization_id,
                "user:reader", frozenset({"reader"}), int(time.time()) + 300,
            ))
        result = change_options(service)
        assert change_options(service) == result
        fields = {item["slot_id"]: item for item in result["fields"]}
        assert fields.keys() == bindings.keys()
        assert {"launch_date", "currency", "product_plan"} <= fields.keys()
        assert fields["launch_date"]["value_kind"] == "date"
        for slot, item in fields.items():
            assert item["domain_id"] == bindings[slot].domain_id
            assert item["owner_id"] == bindings[slot].owner_id
            assert item["current"] == {
                "version": sources[slot].version, "digest": sources[slot].digest,
                "state": sources[slot].state.value, "value": sources[slot].payload["canonical_value"],
            }
            if reader:
                assert item["allowed_operations"] == []
                with pytest.raises(AuthenticationError, match="AUTH_ACTION_DENIED"):
                    submit_change(service, ChangeProposalInput(
                        event_id=f"reader-{slot}", slot_id=slot,
                        base_version=item["current"]["version"], base_digest=item["current"]["digest"],
                        value=item["current"]["value"], source_ref="source:reader-inspection",
                    ))
            else:
                assert item["allowed_operations"] == ([] if priced and slot == "currency" else ["UPDATE"])
        assert fields["currency"]["blocked_reason"] == (
            "PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE" if priced else None
        )
        assert before == (service.current_quote(), service.current_snapshot(), service.store.event_records())
        assert sources == {slot: service.store.get_object(binding.object_id) for slot, binding in bindings.items()}
    finally:
        if token is not None:
            request_principal.reset(token)
        service.close()


def run_sources(script: str, language: str = "en") -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require('./tests/workspace/console_dom_harness.js');
document.documentElement.lang=LANGUAGE;
const configuration={execution_run_id:'run:sources',fields:[
 {slot_id:'launch_date',domain_id:'product',label:'上线日期',value_kind:'date',
  current:{version:'v1',digest:'sha256:date',state:'CURRENT',value:'2026-10-01'},owner_id:'owner:product',allowed_operations:['UPDATE']},
 {slot_id:'currency',domain_id:'finance',label:'报价币种',value_kind:'text',
  current:{version:'v2',digest:'sha256:currency',state:'CURRENT',value:'GBP'},owner_id:'owner:finance',allowed_operations:[],blocked_reason:'PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE'},
 {slot_id:'product_plan',label:'产品方案',value_kind:'text',
  current:{version:'v3',digest:'sha256:plan',state:'CURRENT',value:'Enterprise'},owner_id:'owner:product',allowed_operations:['UPDATE']},
]};
const calls=[],session={mode:'local',principal:null};let failure=false;
window.OrgRebaseClient={session:()=>session,workspace:()=>'workspace:one',async json(path,request={}){
 calls.push({path,request});
 if(failure)throw new Error('READ_UNAVAILABLE');
 if(path.endsWith('/change-options'))return structuredClone(configuration);
 if(path.includes('/changes?'))return {items:[],next_cursor:null};
 throw new Error(path);
}};
const source=SOURCE;
assert(source.indexOf('id="change-source-context"')<source.indexOf('<form id="change-proposal-form"'),
 'read-only source context must be outside the command form');
vm.runInNewContext(source,{window,document,CustomEvent,performance,crypto});
const refresh=()=>window.OrgRebaseChangeWorkbench.refresh();
const select=async slot=>{nodes.get('change-field').value=slot;await nodes.get('change-field').emit('change');};
const input=async(id,value)=>{nodes.get(id).value=value;await nodes.get(id).emit('input');};
const language=value=>{document.documentElement.lang=value;window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));};
const choices=()=>nodes.get('change-field').children.map(item=>item.textContent);
(async()=>{
 await tick();await tick();
 SCRIPT
 assert.equal(calls.filter(call=>call.request.method==='POST').length,0,'inspection, refresh and language changes never submit commands');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("SOURCE", json.dumps((ROOT / "demo/console/change-workbench.js").read_text()))
        .replace("LANGUAGE", json.dumps(language)).replace("SCRIPT", script))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_blocked_currency_keeps_current_source_visible_and_date_proposable(language):
    run_sources(r'''
 const saved=JSON.stringify(configuration),zh=document.documentElement.lang!=='en';
 assert.deepEqual(choices(),zh
  ? ['产品 · 上线日期','财务 · 报价币种\uff08当前不可提案\uff09','产品方案']
  : ['Product · Launch date','Finance · Currency (proposal unavailable)','Product plan']);
 assert.equal(nodes.get('change-value').type,'date');
 assert.equal(nodes.get('change-current-value').textContent,'2026-10-01');
 assert.equal(nodes.get('change-proposal-form').hidden,false);
 assert.equal(nodes.get('change-submit').disabled,false);
 await select('currency');
 assert.equal(nodes.get('change-field').children[1].disabled,false,'blocked fields remain inspectable');
 assert.equal(nodes.get('change-source-context').hidden,false);
 assert.equal(nodes.get('change-current-value').textContent,'GBP');
 assert.equal(nodes.get('change-owner-value').textContent,'owner:finance');
 assert.equal(nodes.get('change-base-value').textContent,'v2 · CURRENT');
 assert.equal(nodes.get('change-edit-blocked').hidden,false);
 assert(nodes.get('change-edit-blocked').textContent.includes(zh?'暂不支持单独修改':'cannot be changed independently'));
 assert.equal(nodes.get('change-proposal-form').hidden,true);
 assert.equal(nodes.get('change-submit').disabled,true);
 await nodes.get('change-proposal-form').emit('submit');
 await refresh();language(zh?'en':'zh-CN');
 assert.equal(nodes.get('change-field').value,'currency');
 assert.equal(nodes.get('change-current-value').textContent,'GBP');
 assert.equal(nodes.get('change-submit').disabled,true);
 assert(nodes.get('change-edit-blocked').textContent.includes(zh?'cannot be changed independently':'暂不支持单独修改'));
 await nodes.get('change-proposal-form').emit('submit');
 assert.equal(JSON.stringify(configuration),saved,'read rendering preserves source values, binding domains and permissions');
 // A supplied authority domain wins over any assumption based on the slot name.
 configuration.fields[0].domain_id='legal';await refresh();
 assert.equal(choices()[0],zh?'Legal · Launch date':'法务 · 上线日期');
 await select('launch_date');
 assert.equal(nodes.get('change-value').type,'date');assert.equal(nodes.get('change-submit').disabled,false);
''', language)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_source_inspection_preserves_drafts_and_fails_closed_when_permission_or_reads_change():
    run_sources(r'''
 await input('change-value','2026-11-30');await input('change-source','source:product-release');
 await input('change-proposal-reason','Upstream release moved');
 configuration.fields[0].allowed_operations=[];await refresh();language('zh-CN');
 assert(choices()[0].endsWith('\uff08当前不可提案\uff09'));
 assert.equal(nodes.get('change-value').value,'2026-11-30');
 assert.equal(nodes.get('change-source').value,'source:product-release');
 assert.equal(nodes.get('change-proposal-reason').value,'Upstream release moved');
 assert.equal(nodes.get('change-current-value').textContent,'2026-10-01');
 assert.equal(nodes.get('change-owner-value').textContent,'owner:product');
 assert.equal(nodes.get('change-submit').disabled,true);
 assert(nodes.get('change-edit-blocked').textContent.includes('当前身份或来源状态'));
 await nodes.get('change-proposal-form').emit('submit');
 configuration.fields[0].allowed_operations=['UPDATE'];await refresh();language('en');
 assert.equal(nodes.get('change-value').value,'2026-11-30');assert.equal(nodes.get('change-submit').disabled,false);
 failure=true;await refresh();
 assert.equal(nodes.get('change-source-context').hidden,true);
 assert.equal(nodes.get('change-current-value').textContent,'','failed reads must not present old facts as current');
 assert.equal(nodes.get('change-proposal-form').hidden,true);assert.equal(nodes.get('change-submit').disabled,true);
 await nodes.get('change-proposal-form').emit('submit');
 failure=false;await refresh();
 assert.equal(nodes.get('change-value').value,'2026-11-30');
 assert.equal(nodes.get('change-source').value,'source:product-release');
 assert.equal(nodes.get('change-source-context').hidden,false);
 assert.equal(nodes.get('change-current-value').textContent,'2026-10-01');
 assert.equal(nodes.get('change-submit').disabled,false);
 // A reader with no draft can inspect every source without gaining an operation.
 configuration.fields.forEach(item=>item.allowed_operations=[]);await refresh();
 await select('product_plan');
 assert.equal(nodes.get('change-current-value').textContent,'Enterprise');
 assert.equal(nodes.get('change-owner-value').textContent,'owner:product');
 assert.equal(nodes.get('change-proposal-form').hidden,true);assert.equal(nodes.get('change-submit').disabled,true);
 assert.equal(choices()[2],'Product plan (proposal unavailable)','legacy responses without domain_id stay compatible');
 await nodes.get('change-proposal-form').emit('submit');
''')
