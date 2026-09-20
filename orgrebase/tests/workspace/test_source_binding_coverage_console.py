from __future__ import annotations

import json
from pathlib import Path

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const {document,window,CustomEvent,nodes,tick}=require(__HARNESS__);
let now=Date.parse('2026-09-17T10:00:00Z'),serial=0,calls=0,error=null,hold=false,resolve;
const timers=new Map();
window.setTimeout=(fn,delay)=>{const id=++serial;timers.set(id,{fn,delay});return id};
window.clearTimeout=id=>timers.delete(id);
const Clock=class extends Date{static now(){return now}};
const record='12345678-1234-1234-1234-123456789abc';
let value={schema_version:'orgrebase.source-binding-view.v1',record_ids:[record],
 status:'CONFIRMED',allowed_actions:[],candidates:[],gaps:[],inventory:null,proposal:null,
 coverage:{schema_version:'orgrebase.source-coverage.v1',status:'COMPLETE',reasons:[],
 record_ids:[record],fields:['launchdate','currency'],observed_at:'2026-09-17T09:59:00Z',
 expires_at:'2026-09-17T10:01:00Z',records:{secret:'RAW_FIELD_VALUE'},page_ref:'/private/cursor',
 cursor_digest:'SECRET_CURSOR',coverage_digest:'PRIVATE_DIGEST'}};
window.OrgRebaseClient={async json(path,options){
 assert.equal(path,'/api/workspace/source-binding');assert.equal(options,undefined,'only existing GET is used');
 calls++;if(error)throw error;if(hold)return new Promise(done=>{resolve=done});return structuredClone(value);
}};
vm.runInNewContext(__SOURCE__,{window,document,CustomEvent,Date:Clock});
const contents=node=>[node.textContent||'',...node.children.map(contents)].join(' ');
const coverage=()=>nodes.get('source-binding-coverage');
const refresh=()=>window.OrgRebaseSourceBinding.refresh();
'''


def run_coverage_node(script: str) -> None:
    run_node((HARNESS + '\n(async()=>{\n' + script + r'''
})().catch(error=>{console.error(error);process.exitCode=1});
''').replace('__SOURCE__', json.dumps((ROOT / 'demo/console/source-binding.js').read_text()))
       .replace('__HARNESS__', json.dumps(str(ROOT / 'tests/workspace/console_dom_harness.js'))))


def test_coverage_shows_scoped_evidence_and_expires_without_request_or_write():
    run_coverage_node(r'''
assert.equal(coverage().hidden,true);
await refresh();
assert.equal(coverage().dataset.status,'COMPLETE');
let text=contents(coverage());
for(const expected of [record,'launchdate · currency','2026-09-17 09:59:00 UTC',
 '2026-09-17 10:01:00 UTC','Complete for the listed scope',
 'Mapping confirmation and source synchronization are checked separately',
 'applying changes still requires business approval'])assert(text.includes(expected),expected);
for(const secret of ['RAW_FIELD_VALUE','/private/cursor','SECRET_CURSOR','PRIVATE_DIGEST'])assert(!text.includes(secret));
assert.equal(timers.size,1);assert.equal([...timers.values()][0].delay,60001);
now=Date.parse('2026-09-17T10:01:01Z');[...timers.values()][0].fn();
assert.equal(coverage().dataset.status,'UNKNOWN');assert.equal(timers.size,0);
text=contents(coverage());assert(text.includes('Source read evidence has expired'));
assert(text.includes('synchronize the source again'));assert(!text.includes('Complete for the listed scope'));
assert.equal(calls,1,'local expiry cannot run synchronization or poll');
value.coverage.observed_at='2026-09-17T10:01:00Z';value.coverage.expires_at='2026-09-17T10:06:00Z';
await nodes.get('source-binding-refresh').emit('click');
assert.equal(coverage().dataset.status,'COMPLETE');assert.equal(calls,2);
''')


def test_missing_source_configuration_has_a_translatable_recoverable_state():
    run_coverage_node(r'''
error={code:'SOURCE_CONFIG_ABSENT'};await refresh();
assert.equal(nodes.get('source-binding-status').textContent,'Enterprise source is not configured');
assert(contents(nodes.get('source-binding-notice')).includes('configure a read-only connection'));
document.documentElement.lang='zh-CN';
window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert.equal(nodes.get('source-binding-status').textContent,'尚未配置企业来源');
assert(contents(nodes.get('source-binding-notice')).includes('操作不会自动重发'));
error=null;await refresh();
assert(nodes.get('source-binding-status').textContent.startsWith('映射已确认'));
assert.equal(nodes.get('source-binding-notice').hidden,true);
''')


def test_incomplete_coverage_has_bilingual_allowlisted_actions_without_raw_errors():
    run_coverage_node(r'''
document.documentElement.lang='zh-CN';
value.coverage.status='UNKNOWN';value.coverage.reasons=[
 'SOURCE_PAGINATION_INCOMPLETE','SOURCE_RECORDS_NOT_OBSERVED','SOURCE_CURRENT_READBACK_REQUIRED',
 'SOURCE_PERMISSION_DENIED','https://private.example/secret?token=PRIVATE_TOKEN','__proto__'];
await refresh();let text=contents(coverage());
for(const expected of ['来源分页尚未读取完','完成剩余分页后刷新','部分范围内记录尚未观察到',
 '当前记录回读尚未完成','来源读取权限不足','来源读取证据尚不能确认'])assert(text.includes(expected),expected);
assert.equal(coverage().dataset.status,'UNKNOWN');assert.equal(timers.size,0);
document.documentElement.lang='en';window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
text=contents(coverage());
for(const expected of ['Source pagination is incomplete','all pages are processed','Some scoped records have not been observed',
 'Current-record readback is incomplete','Source read access was denied','Source read evidence is unverified'])assert(text.includes(expected),expected);
for(const raw of ['PRIVATE_TOKEN','private.example','__proto__','SOURCE_PERMISSION_DENIED'])assert(!text.includes(raw));
assert.equal(calls,1,'language changes only rerender');
error=Object.assign(new Error('/private/path/PRIVATE_ERROR'),{code:'UNKNOWN_PRIVATE_ERROR'});
await refresh();text=contents(nodes.get('source-binding-notice'));
assert(text.includes('No operation is replayed.'));assert(!text.includes('PRIVATE_ERROR'));
assert.equal(coverage().hidden,true);assert.equal(coverage().children.length,0);
''')


def test_mapping_confirmation_alone_and_invalid_coverage_cannot_claim_complete_reads():
    run_coverage_node(r'''
const complete=structuredClone(value.coverage);
for(const invalid of [undefined,{...complete,schema_version:'unrecognized'},
 {...complete,record_ids:[]},{...complete,fields:['/private/field']},
 {...complete,reasons:'invalid'},{...complete,reasons:[null]},
 {...complete,observed_at:'PRIVATE_TIME'}, {...complete,expires_at:'2026-09-17T09:58:00Z'},
 {...complete,status:'UNKNOWN',reasons:[]}]){
 value.coverage=invalid;await refresh();
 assert.equal(coverage().dataset.status,'UNKNOWN');assert.equal(timers.size,0);
 assert(!contents(coverage()).includes('Complete for the listed scope'));
 assert(!contents(coverage()).includes('PRIVATE_TIME'));assert(!contents(coverage()).includes('/private/field'));
}
value.coverage=undefined;value.status='DISCOVERY_REQUIRED';await refresh();
assert(contents(coverage()).includes('Field discovery has not been recorded'));
value.status='CONFIRMED';await refresh();
assert(contents(coverage()).includes('No initial synchronization has been committed'));
''')


def test_larger_valid_scopes_keep_their_actual_count_and_have_explicit_display_limits():
    run_coverage_node(r'''
value.coverage.record_ids=Array.from({length:40},(_,i)=>`12345678-1234-1234-1234-${String(i).padStart(12,'0')}`);
value.coverage.fields=Array.from({length:40},(_,i)=>`field_${i}`);
await refresh();const text=contents(coverage());
assert.equal(coverage().dataset.status,'COMPLETE');
assert(text.includes('Recorded scope: 40 records · 40 fields'));
assert(text.includes('Showing the first 32 of 40 records.'));assert(text.includes('Showing the first 32 of 40 fields.'));
assert(text.includes(value.coverage.record_ids[31]));assert(!text.includes(value.coverage.record_ids[32]));
assert(text.includes('field_31'));assert(!text.includes('field_32'));
''')


def test_coverage_scope_and_expiry_timer_are_cleared_at_session_boundary():
    run_coverage_node(r'''
await refresh();assert(contents(coverage()).includes(record));assert.equal(timers.size,1);
hold=true;const pending=refresh();await tick();
window.dispatchEvent(new CustomEvent('orgrebase:sessionchange',{detail:{mode:'oidc',authenticated:true,
 principal:{actor_id:'owner:b',tenant_id:'tenant:b'}}}));
assert.equal(coverage().hidden,true);assert.equal(coverage().children.length,0);assert.equal(timers.size,0);
resolve(structuredClone(value));await pending;
assert.equal(coverage().hidden,true);assert(!contents(coverage()).includes(record));assert.equal(timers.size,0);
hold=false;await refresh();assert.equal(coverage().hidden,false);
window.dispatchEvent(new CustomEvent('orgrebase:sessionended'));
assert.equal(coverage().hidden,true);assert.equal(coverage().children.length,0);assert.equal(timers.size,0);
''')
