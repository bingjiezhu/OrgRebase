"""Exercise the shipped Skill panel with the real backend response contract."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_validation import execute_validation, validation_view

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { document, window, nodes, tick, CustomEvent } = require('./tests/workspace/console_dom_harness.js');
const mount = document.createElement('div'); mount.id = 'skill-validation-mount';
const passed = JSON.parse(fs.readFileSync(0, 'utf8'));
const initial = { ...passed, state: 'NOT_RUN', can_execute: true, result: null };
let session = { mode: 'local', authenticated: false, authentication_required: false };
let workspace = initial.binding.workspace_id;
let handler;
const calls = [];
window.OrgRebaseClient = { session: () => session, workspace: () => workspace,
  json: async (path, options = {}) => { calls.push({path, options}); return handler(path, options); } };
const text = node => (node.textContent || '') + node.children.map(text).join(' ');
const boot = () => vm.runInNewContext(fs.readFileSync('demo/console/skill-validation.js', 'utf8'),
  {window,document,CustomEvent});
const run = () => nodes.get('skill-validation-run');
const refresh = () => nodes.get('skill-validation-refresh');
const settle = async () => { await tick(); await tick(); };
const postCount = () => calls.filter(call => call.options.method === 'POST').length;
"""


@pytest.fixture(scope="module")
def completed_view(tmp_path_factory):
    service = WorkspaceService(store_path=tmp_path_factory.mktemp("skill-console") / "state.sqlite")
    try:
        view = validation_view(service)
        return execute_validation(service, actor_id="human:skill-steward",
            expected_package_digest=view["binding"]["package_digest"],
            expected_predecessor_digest=view["binding"]["predecessor_digest"],
            expected_catalog_digest=view["binding"]["compatibility_catalog_digest"])
    finally:
        service.close()


def check(view, script):
    node = shutil.which("node")
    assert node, "Node is required to exercise the shipped console module"
    result = subprocess.run([node, "-e", HARNESS + "\n(async()=>{\n" + script +
        "\n})().catch(error=>{console.error(error);process.exitCode=1;});"],
        input=json.dumps(view), text=True, capture_output=True, cwd=ROOT, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_only_explicit_click_executes_and_completed_receipt_survives_language_change(completed_view):
    check(completed_view, r"""
handler = (_, options) => options.method === 'POST' ? passed : initial;
boot(); await settle();
assert.equal(postCount(), 0); assert.equal(run().disabled, false);
await run().emit('click');
assert.equal(postCount(), 1); assert.equal(run().disabled, true);
assert.match(text(mount), /Recovery executed/);
assert.match(text(mount), /same executable program/);
const request = calls.find(call => call.options.method === 'POST');
assert.equal(request.options.body.expected_package_digest, passed.binding.package_digest);
assert.equal(request.options.body.expected_predecessor_digest, passed.binding.predecessor_digest);
assert.equal(request.options.headers['X-OrgRebase-Actor'], 'human:skill-steward');
assert.equal(request.options.body.expected_catalog_digest, passed.binding.compatibility_catalog_digest);
assert.match(text(mount), /6 compatibility checks passed/);
document.documentElement.lang = 'zh-CN';
window.dispatchEvent(new CustomEvent('orgrebase:languagechange'));
assert.match(text(mount), /执行恢复/); assert.equal(postCount(), 1);
""")


@pytest.mark.parametrize("mutation", [
    "value.result.evidence = null;",
    "value.result.binding = {...value.result.binding, workspace_id:'other'};",
    "value.result.evidence.rollback.receipt.effective_package_digest='sha256:'+'0'.repeat(64);",
    "value.result.evidence.evaluation.case_results.pop();",
    "value.result.evidence.evaluation.case_results[8]=value.result.evidence.evaluation.case_results[5];",
    "value.result.evidence.evaluation.case_results[8].partition='REPLAY';",
    "value.result.evidence.evaluation.premise_lock.qualification_suite_digest='sha256:'+'0'.repeat(64);",
    "value.binding.qualification_suite_revision='legacy'; value.result.binding.qualification_suite_revision='legacy';",
])
def test_incomplete_or_cross_bound_pass_response_never_renders_success(completed_view, mutation):
    check(completed_view, "const value=structuredClone(passed);\n" + mutation + r"""
handler = () => value;
boot(); await settle();
assert.equal(run().disabled, true); assert.equal(postCount(), 0);
assert.doesNotMatch(text(mount), /Controlled validation passed|Recovery executed/);
assert.match(text(mount), /SKILL_VALIDATION_UNAVAILABLE|SKILL_VALIDATION_SCOPE_INVALID/);
""")


@pytest.mark.parametrize("pending_command", [False, True])
def test_late_result_after_session_switch_cannot_restore_other_workspace(completed_view, pending_command):
    check(completed_view, f"const pendingCommand={str(pending_command).lower()};\n" + r"""
let release;
const late = new Promise(resolve=>{release=resolve;});
let initialRequest = true;
handler = (_, options) => {
  if (options.method === 'POST' || !pendingCommand && initialRequest) { initialRequest=false; return late; }
  return initial;
};
boot(); await settle();
let submitting;
if (pendingCommand) { submitting=run().emit('click'); await settle(); }
workspace = 'different-workspace';
session = {mode:'oidc',authenticated:true,authentication_required:true,
  principal:{tenant_id:'org:other',actor_id:'human:reader',subject:'other-subject'}};
const other = {...initial,binding:{...initial.binding,workspace_id:workspace,tenant_id:'org:other'},can_execute:false};
handler = () => other;
window.dispatchEvent(new CustomEvent('orgrebase:sessionchange')); await settle();
release(passed); if(submitting) await submitting; await settle();
assert.equal(run().disabled,true);
assert.doesNotMatch(text(mount), /Controlled validation passed|Recovery executed/);
assert.match(text(mount), /different-workspace/);
assert.equal(postCount(), pendingCommand ? 1 : 0);
""")


def test_unknown_post_result_requires_read_reconciliation_and_does_not_resend(completed_view):
    check(completed_view, r"""
handler = (_, options) => {
  if (options.method === 'POST') {const error = new Error('lost response');error.code='NETWORK_UNKNOWN';throw error;}
  return initial;
};
boot(); await settle(); await run().emit('click');
assert.equal(postCount(),1); assert.equal(run().disabled,true);
handler = () => ({...initial,state:'RESULT_UNKNOWN',can_execute:false});
await refresh().emit('click');
assert.equal(postCount(),1); assert.equal(run().disabled,true);
assert.match(text(mount), /unconfirmed/);
""")


def test_golden_topology_recognizes_only_bound_current_or_retained_legacy_qualification(completed_view):
    check(completed_view, r"""
const source = fs.readFileSync('demo/console/app.js', 'utf8');
const expression = source.split('const skillQualificationReady = ')[1].split(';')[0];
const matches = new Function('skill', 'return ' + expression);
const current = {evaluation_partition_count:8,evaluation_case_count:9,
  qualification_suite_revision:passed.binding.qualification_suite_revision,
  qualification_suite_digest:passed.binding.qualification_suite_digest};
assert.equal(matches(current), true);
assert.equal(matches({evaluation_partition_count:8}), true);
assert.equal(matches({evaluation_partition_count:9}), false);
assert.equal(matches({...current,evaluation_partition_count:9}), false);
assert.equal(matches({...current,evaluation_case_count:8}), false);
assert.equal(matches({...current,evaluation_case_count:undefined}), false);
assert.equal(matches({...current,qualification_suite_digest:'sha256:'+'0'.repeat(64)}), false);
assert.equal(matches({...current,qualification_suite_revision:'unrecognized'}), false);
""")
