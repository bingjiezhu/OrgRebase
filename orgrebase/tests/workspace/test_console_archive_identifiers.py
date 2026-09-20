from __future__ import annotations

from tests.workspace.test_workspace_client import run_node


def test_archive_identifiers_remain_inspectable_and_clear_with_stale_archive():
    run_node(r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
const source = fs.readFileSync('demo/console/app.js', 'utf8');
const ids = ['current-run-archive-id', 'current-run-archive-quote', 'current-run-archive-authority'];
const nodes = new Map();
const byId = id => {
  if (!nodes.has(id)) nodes.set(id, {dataset: {}, textContent: '', removeAttribute(key) { delete this[key]; }});
  return nodes.get(id);
};
const context = {byId, t: key => key, displayToken: value => `display:${value}`,
  currentRunArchiveProof: view => view.valid === true,
  terminalRunId: state => state.runId};
vm.createContext(context);
for (const name of ['text', 'exactAuditTitle', 'clearCurrentRunArchive', 'renderCurrentRunArchive']) {
  const start = source.indexOf(`function ${name}(`);
  let end = source.indexOf('\nfunction ', start + 1);
  if (name === 'clearCurrentRunArchive') end = source.indexOf('\nconst verifiedApprovalAuthorities', start);
  vm.runInContext(source.slice(start, end), context);
}
const runId = 'run:enterprise-quote:' + 'long-identity-'.repeat(12);
const quoteRef = 'artifact:quote:' + 'customer-identity-'.repeat(12) + '@v2';
const authority = 'ORGREBASE_CONTROL_PLANE';
const archive = {valid: true, record: {quote: {ref: quoteRef, launch_date: '2026-10-15', currency: 'EUR'},
  selective_rebase_receipts: [{}], human_approval_count: 1, canonical_authority: authority}};
context.renderCurrentRunArchive(null, {runId});
assert.equal(byId(ids[0]).title, runId);
context.renderCurrentRunArchive(archive, {runId});
for (const [id, exact] of [[ids[0], runId], [ids[1], quoteRef], [ids[2], authority]]) {
  assert.equal(byId(id).title, exact);
}
assert.equal(byId(ids[0]).textContent, runId);
assert.equal(byId(ids[1]).textContent, quoteRef);
for (const [view, state] of [[{valid: false}, {runId}], [null, {runId: null}], [null, {runId: 'run:next'}]]) {
  context.renderCurrentRunArchive(archive, {runId});
  context.renderCurrentRunArchive(view, state);
  for (const id of ids.slice(1)) {
    assert.equal(byId(id).textContent, '—');
    assert.equal(byId(id).title, undefined);
  }
  assert.equal(byId(ids[0]).title, view || !state.runId ? undefined : state.runId);
}
''')
