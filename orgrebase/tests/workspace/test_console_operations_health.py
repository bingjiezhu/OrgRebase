"""Exercise the visible-page health refresh and lifecycle using the shipped JavaScript."""
from __future__ import annotations

import shutil

import pytest

from tests.workspace.test_workspace_client import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")

HARNESS = r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
const app = fs.readFileSync('demo/console/app.js', 'utf8');
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
function harness({honorAbort = true, language = null} = {}) {
 const calls = [], timers = new Map(), listeners = new Map(), rendered = {}, titles = {};
 let now = 0, timerId = 0;
 function listen(type, callback) { listeners.set(type, [...(listeners.get(type) || []), callback]); }
 function emit(type, detail = {}) { for (const callback of listeners.get(type) || []) callback(detail); }
 const document = {hidden: false, addEventListener: listen};
 const window = {addEventListener: listen,
  setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, due: now + delay}); return id; },
  clearTimeout(id) { timers.delete(id); },
 };
 const context = vm.createContext({window, document, AbortController, Date: {now: () => now},
  currentState: null, currentHealth: null, currentReadiness: null, workspaceStateAvailability: 'loading',
  UNAVAILABLE_WORKSPACE_PROJECTION: {}, eventCount: () => 0,
  projectedEventScopes: () => ({global: {}}), exactAuditTitle: (id, value) => { titles[id] = value || ''; }, t: value => value, displayToken: value => value,
  text: (id, value) => { rendered[id] = value; }, localizedText: (id, value) => { rendered[id] = value; },
  api(path, {signal} = {}) { return new Promise((resolve, reject) => {
   calls.push({path, signal, resolve, reject});
   if (honorAbort) signal.addEventListener('abort', () => reject(new Error('ABORTED')), {once: true});
  }); },
 });
 vm.runInContext(app.slice(app.indexOf('function shortDigest('), app.indexOf('function escapeHtml(')), context);
 if (language) {
  vm.runInContext('const DEFAULT_LANGUAGE = "zh-CN";' + app.slice(app.indexOf('const I18N ='), app.indexOf('function modelSuggestion(')), context);
  vm.runInContext(`currentLanguage = ${JSON.stringify(language)}`, context);
 }
 vm.runInContext(app.slice(app.indexOf('function renderOperations('),
  app.indexOf('  const evidence = currentRetainedEvidence', app.indexOf('function renderOperations('))) + '}', context);
 vm.runInContext(app.slice(app.indexOf('function readinessProbeResult(')), context);
 async function settle(start, version = '0.4.0', ready = {status: 'ready'}) {
  calls[start].resolve({status: 'ok', version}); calls[start + 1].resolve(ready); await tick();
 }
 async function advance(ms) {
  now += ms;
  for (const [id, timer] of [...timers]) if (timer.due <= now && timers.delete(id)) timer.callback();
  await tick();
 }
 return {context, calls, timers, rendered, titles, emit, settle, advance,
  async visibility(hidden) { document.hidden = hidden; emit('visibilitychange'); await tick(); }};
}
'''


def test_visible_health_refreshes_after_failure_and_recovers_the_deployed_version() -> None:
    run_node(HARNESS + r'''
(async () => {
 const h = harness(); assert.deepEqual(h.calls.map(call => call.path), ['/api/health', '/readyz']);
 await h.settle(0); assert.equal(h.rendered['ops-ready'], 'READY');
 assert.match(h.rendered['ops-health-detail'], /0\.4\.0/);
 for (let i = 0; i < 50; i++) {
  h.emit('orgrebase:staterendered'); h.emit('orgrebase:stateunavailable'); h.emit('hashchange');
 }
 await tick(); assert.equal(h.calls.length, 2, 'events must not bypass the refresh budget');
 assert.equal(h.timers.size, 1, 'one future refresh');
 await h.advance(30000);
 assert.equal(h.calls.length, 4); assert.equal(h.rendered['ops-ready'], 'CHECKING');
 h.calls[2].reject(new Error('NETWORK_DOWN')); h.calls[3].reject(new Error('NETWORK_DOWN'));
 await tick(); assert.equal(h.rendered['ops-health'], 'UNAVAILABLE'); assert.equal(h.rendered['ops-ready'], 'UNAVAILABLE');
 await h.advance(30000); await h.settle(4, '0.4.1');
 assert.equal(h.rendered['ops-ready'], 'READY'); assert.match(h.rendered['ops-health-detail'], /0\.4\.1/);
 assert.equal(h.timers.size, 1);
 await h.visibility(true); assert.equal(h.timers.size, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_health_requests_coalesce_and_abort_on_timeout_without_keeping_ready() -> None:
    run_node(HARNESS + r'''
(async () => {
 const h = harness(); await h.settle(0); await h.advance(30000);
 const pending = h.context.refreshOperationsHealth();
 for (let i = 0; i < 50; i++) assert.equal(h.context.refreshOperationsHealth(), pending);
 assert.equal(h.calls.length, 4, 'one pair of probes in flight');
 await h.advance(5000); await pending;
 assert.equal(h.calls[2].signal.aborted, true); assert.equal(h.calls[3].signal.aborted, true);
 assert.equal(h.rendered['ops-ready'], 'UNAVAILABLE'); assert.equal(h.timers.size, 1);
 await h.advance(30000); await h.settle(4);
 assert.equal(h.rendered['ops-ready'], 'READY'); await h.visibility(true);
 assert.equal(h.timers.size, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_hidden_and_restored_pages_discard_late_responses_and_release_timers() -> None:
    run_node(HARNESS + r'''
(async () => {
 const h = harness({honorAbort: false});
 await h.visibility(true);
 assert.equal(h.calls[0].signal.aborted, true); assert.equal(h.timers.size, 0);
 assert.equal(h.context.currentReadiness, null);
 await h.advance(60000); assert.equal(h.calls.length, 2, 'hidden pages do not probe');
 await h.visibility(false); assert.equal(h.calls.length, 4);
 await h.settle(2, 'new-version'); await h.settle(0, 'obsolete-version');
 assert.match(h.rendered['ops-health-detail'], /new-version/);
 assert.equal(h.timers.size, 1, 'obsolete finalizers must not add another timer');
 h.emit('pagehide'); assert.equal(h.timers.size, 0);
 h.emit('orgrebase:staterendered'); await h.advance(60000); assert.equal(h.calls.length, 4);
 h.emit('pageshow', {persisted: true}); assert.equal(h.calls.length, 6, 'BFCache restore refreshes immediately');
 await h.settle(4, 'restored-version'); assert.match(h.rendered['ops-health-detail'], /restored-version/);
 await h.visibility(true); assert.equal(h.timers.size, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_refresh_renders_current_readiness_rejection_and_malformed_health() -> None:
    run_node(HARNESS + r'''
(async () => {
 const h = harness(); await h.settle(0); await h.advance(30000);
 h.calls[2].resolve(null);
 h.calls[3].reject({httpStatus: 503, payload: {detail: {code: 'STORE_NOT_READY'}}});
 await tick(); assert.equal(h.rendered['ops-health'], 'UNAVAILABLE');
 assert.equal(h.rendered['ops-ready'], 'operations.ready.notReady');
 await h.visibility(true); assert.equal(h.timers.size, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_storage_card_requires_current_workspace_and_readiness_without_guessing_database() -> None:
    run_node(HARNESS + r'''
(async () => {
 const h = harness();
 assert.equal(h.rendered['ops-persistence'], 'CHECKING');
 await h.settle(0);assert.equal(h.rendered['ops-persistence'], 'CHECKING', 'probe alone cannot establish workspace access');
 h.context.currentState = {stage:'EMPTY'};h.context.workspaceStateAvailability='ready';
 h.context.currentHealth.profile='AUTHENTICATED_SINGLE_TENANT';
 h.context.renderOperations(h.context.currentState);
 assert.equal(h.rendered['ops-persistence'], 'READY');
 assert.equal(h.rendered['ops-persistence-detail'], 'operations.persistence.ready');
 assert(!JSON.stringify(h.rendered).includes('SQLITE'), 'authenticated PostgreSQL is not labelled SQLite');
 h.context.currentState={quote:{version:'v1'},stage:'FORMED'};
 await h.advance(30000);assert.equal(h.rendered['ops-persistence'], 'CHECKING');
 h.calls[2].reject(new Error('NETWORK_DOWN'));h.calls[3].reject(new Error('NETWORK_DOWN'));await tick();
 assert.equal(h.rendered['ops-persistence'],'UNAVAILABLE','retained quote cannot keep a stale ready/reopenable claim');
 await h.advance(30000);await h.settle(4);assert.equal(h.rendered['ops-persistence'],'READY');
 h.context.workspaceStateAvailability='unavailable';h.context.currentState=null;
 h.context.renderOperations({});assert.equal(h.rendered['ops-persistence'],'UNAVAILABLE', 'public probe does not prove access to an unavailable workspace');
 assert.equal(h.rendered['ops-persistence-detail'],'operations.persistence.unavailable');
 await h.visibility(true);assert.equal(h.timers.size,0);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_live_deployment_details_do_not_borrow_frozen_or_production_claims(language: str) -> None:
    run_node(HARNESS + "const language = " + repr(language) + r''';
(async () => {
 const h = harness({language});
 const profile = 'sha256:' + '1'.repeat(64), pack = 'sha256:' + '2'.repeat(64);
 await h.settle(0, '0.4.0', {status:'ready',workspace_store:'READY',
  deployment_maturity:'SINGLE_ENTERPRISE_PILOT',production_ready:false,
  profile_digest:profile,enterprise_pack_digest:pack});
 let detail = h.rendered['ops-ready-detail'];
 assert.match(detail, language === 'en' ? /Single-enterprise pilot/ : /单企业试点/);
 assert.match(detail, language === 'en' ? /Production readiness not claimed/ : /未声明生产就绪/);
 assert(detail.includes(profile.slice(0,12)) && detail.includes(pack.slice(0,12)));
 assert(h.titles['ops-ready-detail'].includes(profile) && h.titles['ops-ready-detail'].includes(pack));
 await h.advance(30000);
 assert.equal(h.titles['ops-ready-detail'], '', 'old deployment refs cleared while checking');
 await h.settle(2, '0.4.1', {status:'ready',service:'orgrebase',version:'0.4.1'});
 detail = h.rendered['ops-ready-detail'];
 assert.equal(detail, language === 'en' ? 'Service readiness probe passed' : '服务就绪探针已通过');
 assert.equal(h.titles['ops-ready-detail'], '', 'restricted production probe has no pack or maturity claim');
 await h.advance(30000);
 await h.settle(4, '0.4.1', {status:'ready',production_ready:'false',profile_digest:'bad',enterprise_pack_digest:'<script>'});
 assert.equal(h.rendered['ops-ready-detail'], detail, 'invalid or missing fields are not interpreted as evidence');
 assert.equal(h.titles['ops-ready-detail'], '');
 await h.advance(30000);
 await h.settle(6, '0.4.1', {status:'not_ready',deployment_maturity:'SINGLE_ENTERPRISE_PILOT',profile_digest:profile});
 assert(!h.rendered['ops-ready-detail'].includes(profile.slice(0,12)));
 assert.equal(h.titles['ops-ready-detail'], '');
 await h.visibility(true);
})().catch(error => {console.error(error); process.exitCode=1;});
''')
