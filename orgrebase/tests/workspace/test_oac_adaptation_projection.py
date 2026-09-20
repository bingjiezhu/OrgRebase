"""Regression checks for the shipped OAC view, without model or business calls."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("demo/console/oac-adaptation.js", "utf8");
const kinds = ["DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"];
const ackIds = ["oac-ack-summary", "oac-ack-unknowns", "oac-ack-boundary"];
function prepared() {
  return {
    status: "OWNER_REVIEW_PENDING",
    adaptation: {
      status: "OWNER_REVIEW_PENDING", adaptation_run_id: "run:adaptation", candidate_digest: "sha256:candidate",
      owner_ref: "owner:one", owner_review_summary_digest: "sha256:summary", review_remaining_ms: 0,
      candidate_mappings: kinds.map(component_kind => ({ component_kind, source_root_ref: "source:" + component_kind })),
      gaps: [], owner_review_summary: {
        digest: "sha256:summary", candidate_mapping_set_digest: "sha256:candidate",
        component_reviews: kinds.map(component_kind => ({ component_kind })),
      },
    },
    agent_mapping: { status: "NOT_RUN", model_status: "NOT_RUN" },
    execution_policy: {
      mode: "OFFLINE_LOCAL", mapping_source: "STRUCTURED_MATERIALS", mapping_model_provider: "none",
      model_provider: "ollama-local", mapping_will_call_external_model: false,
      shadow_will_call_external_model: false, available_actions: [], blocked_reasons: {},
    },
    workspace_gate: {
      mode: "required", requires_oac_admission: true, form_allowed: false, status: "BLOCKED_PENDING_OAC",
      execution_run_id: "run:current", activation_binding_digest: null, consumption_receipt_digest: null,
    },
  };
}
function admitted() {
  const payload = prepared();
  payload.status = payload.adaptation.status = "READY_FOR_SHADOW";
  payload.adaptation.approval_acknowledgements = [
    "REVIEWED_SOURCE_TO_CONTRACT_SUMMARY", "ACCEPTED_DECLARED_UNKNOWNS", "UNDERSTAND_NO_BUSINESS_APPROVAL",
  ];
  payload.adaptation.lineage_proof = {
    owner_review_summary_digest: "sha256:summary", review_gate_owner_review_summary_digest: "sha256:summary",
    approval_owner_review_summary_digest: "sha256:summary", adapter_capsule_owner_review_summary_digest: "sha256:summary",
    approval_digest: "sha256:approval", adapter_capsule_digest: "sha256:capsule", activation_binding_digest: "sha256:activation",
  };
  payload.workspace_gate.status = "READY_TO_FORM";
  payload.workspace_gate.form_allowed = true;
  payload.workspace_gate.activation_binding_digest = "sha256:activation";
  payload.execution_policy.available_actions = ["EXECUTE_SHADOW"];
  return payload;
}
function response(payload, status = 200) {
  return { ok: status < 400, status, json: async () => payload };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function contextChanged() {
  return Object.assign(new Error("WORKSPACE_REQUEST_CONTEXT_CHANGED"), { code: "WORKSPACE_REQUEST_CONTEXT_CHANGED" });
}
function harness(initial = prepared()) {
  const nodes = new Map(), requests = [], listeners = new Map(), events = [];
  const state = { payload: initial, failGet: false, postResponse: null, getQueue: [], postQueue: [],
    session: { mode: "oidc", authentication_required: true, authenticated: true,
      principal: { issuer: "issuer:test", subject: "subject:owner", tenant_id: "tenant:test",
        actor_id: "owner:one", roles: ["governor"] } },
  };
  function node(id = "") {
    return { id, dataset: {}, textContent: "", hidden: false, checked: false, disabled: false, listeners: {},
      classList: { toggle() {}, add() {}, remove() {} },
      setAttribute(key, value) { this[key] = String(value); }, removeAttribute(key) { delete this[key]; },
      addEventListener(type, callback) { this.listeners[type] = callback; },
      append() {}, replaceChildren() {}, replaceWith() {},
    };
  }
  const byId = id => { if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id); };
  async function fetch(url, options = {}) {
    requests.push({ url, ...options });
    if (options.method === "POST") {
      if (state.postQueue.length) return state.postQueue.shift().promise;
      if (state.postResponse) return { ok: true, status: 200, json: async () => state.postResponse };
      throw new Error("Unexpected command: " + url);
    }
    if (state.getQueue.length) return state.getQueue.shift().promise;
    return state.failGet
      ? { ok: false, status: 503, json: async () => ({ detail: { code: "READ_UNAVAILABLE" } }) }
      : { ok: true, status: 200, json: async () => state.payload };
  }
  const window = {
    OrgRebaseClient: { fetch, readBody: response => response.json(), session: () => state.session }, crypto: { randomUUID: () => "test-command" },
    setInterval: () => 1, clearInterval() {},
    addEventListener(type, callback) { listeners.set(type, callback); }, dispatchEvent(event) { events.push(event); },
  };
  vm.runInNewContext(source, {
    window, document: { documentElement: { lang: "en" }, getElementById: byId, querySelectorAll: () => [], createElement: () => node() },
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    console, Date, Math,
  });
  return { state, byId, requests, events,
    refresh: () => byId("oac-refresh").listeners.click(),
    click: () => byId("oac-primary-action").listeners.click(),
    enhanced: () => byId("oac-enhanced-action").listeners.click(),
    emit: (type, detail) => listeners.get(type)({ detail }),
    deferGet() { const pending = deferred(); state.getQueue.push(pending); return pending; },
    deferPost() { const pending = deferred(); state.postQueue.push(pending); return pending; },
    acknowledge() { ackIds.forEach(id => { byId(id).checked = true; byId(id).listeners.change(); }); },
  };
}
const ready = () => new Promise(resolve => setImmediate(resolve));
"""

SCENARIOS = {
    "session_retirement_clears_contract_and_acknowledgements": r"""
for (const reason of ["logout", "expired", "role-switch"]) {
  const h = harness(); await ready(); h.acknowledge();
  assert.equal(h.byId("oac-primary-action").disabled, false);
  h.state.session = null;
  h.emit("orgrebase:sessionended", {reason});
  assert.equal(h.byId("oac-adaptation").dataset.state, "NOT_STARTED");
  assert.notEqual(h.byId("oac-control-owner").textContent, "owner:one");
  assert.ok(ackIds.every(id => !h.byId(id).checked));
  assert.equal(h.byId("oac-primary-action").disabled, true);
  h.acknowledge(); await h.click(); await h.enhanced();
  assert.equal(h.requests.filter(request => request.method === "POST").length, 0);
}
""",
    "new_identity_failed_read_cannot_retain_or_restore_old_mapping": r"""
const h = harness(); await ready(); h.acknowledge();
const oldRead = h.deferGet(), oldPending = h.refresh();
h.state.session = {...h.state.session, principal: {...h.state.session.principal,
  tenant_id: "tenant:another", subject: "subject:another"}};
h.state.failGet = true;
h.emit("orgrebase:sessionchange", h.state.session); await ready();
assert.equal(h.byId("oac-adaptation").dataset.state, "NOT_STARTED");
assert.match(h.byId("oac-adaptation-status").textContent, /503/);
const eventCount = h.events.length;
oldRead.resolve(response(admitted())); await oldPending;
assert.equal(h.byId("oac-adaptation").dataset.state, "NOT_STARTED");
assert.equal(h.events.length, eventCount, "an old read cannot announce the former identity's contract");
assert.ok(ackIds.every(id => !h.byId(id).checked));
assert.equal(h.byId("oac-refresh").disabled, false);
""",
    "old_commands_cannot_restore_contract_or_unlock_new_identity_actions": r"""
for (const action of ["approve", "shadow"]) {
  const h = harness(action === "approve" ? prepared() : admitted()); await ready(); h.acknowledge();
  const oldCommand = h.deferPost(), oldPending = action === "approve" ? h.click() : h.enhanced();
  h.state.session = {...h.state.session, principal: {...h.state.session.principal, subject: "subject:new"}};
  h.state.payload = prepared();
  h.emit("orgrebase:sessionended", {reason: "role-switch"});
  h.emit("orgrebase:sessionchange", h.state.session); await ready();
  h.acknowledge();
  const newCommand = h.deferPost(), newPending = h.click();
  const eventCount = h.events.length;
  oldCommand.resolve(response(admitted().adaptation)); await oldPending;
  assert.equal(h.byId("oac-adaptation").dataset.state, "OWNER_REVIEW_PENDING");
  assert.equal(h.byId("oac-primary-action").disabled, true, "old finally cannot unlock a new command");
  assert.equal(h.events.length, eventCount, "old result must not announce a state change");
  newCommand.reject(contextChanged()); await newPending;
  assert.equal(h.requests.filter(request => request.method === "POST").length, 2, "one old and one explicit new command, no replay");
}
""",
    "owner_confirmation_requires_current_authenticated_governance_identity": r"""
for (const replaceSession of [
  session => null,
  session => ({...session, authenticated: false}),
  session => ({...session, principal: {...session.principal, roles: ["viewer"]}}),
  session => ({...session, principal: {...session.principal, actor_id: "owner:another"}}),
]) {
  const h = harness(); await ready(); h.acknowledge();
  assert.equal(h.byId("oac-primary-action").disabled, false);
  h.state.session = replaceSession(h.state.session); await h.refresh(); h.acknowledge();
  assert.equal(h.byId("oac-primary-action").disabled, true,
    "complete checkboxes cannot substitute for an authenticated named governor");
  assert.equal(h.requests.filter(request => request.method === "POST").length, 0);
}
const h = harness(); await ready(); h.acknowledge();
const original = h.state.session;
h.state.session = {...original, principal: {...original.principal, actor_id: "owner:another"}};
await h.refresh();
assert.ok(ackIds.every(id => !h.byId(id).checked), "another actor cannot retain the owner's confirmations");
h.state.session = original; await h.refresh();
assert.ok(ackIds.every(id => !h.byId(id).checked));
assert.equal(h.byId("oac-primary-action").disabled, true, "returning owner must confirm again");
h.acknowledge(); assert.equal(h.byId("oac-primary-action").disabled, false);
""",
    "complete_candidate_requires_new_owner_confirmations": r"""
const h = harness(); await ready();
assert.match(h.byId("oac-product-validation").textContent, /PASSED/);
assert.equal(h.byId("oac-primary-action").disabled, true);
h.acknowledge();
assert.equal(h.byId("oac-primary-action").disabled, false);
await h.refresh();
assert.ok(ackIds.every(id => h.byId(id).checked));
assert.equal(h.byId("oac-primary-action").disabled, false);
h.state.payload.adaptation.owner_ref = "owner:another";
await h.refresh();
assert.ok(ackIds.every(id => !h.byId(id).checked));
assert.equal(h.byId("oac-primary-action").disabled, true);
assert.equal(h.requests.filter(request => request.method === "POST").length, 0);
""",
    "missing_or_misbound_review_context_clears_hidden_consent": r"""
for (const change of [
  value => { value.adaptation.owner_review_summary.component_reviews = []; },
  value => { value.adaptation.candidate_mappings = []; },
  value => { value.adaptation.owner_review_summary.component_reviews[4] = { component_kind: "DOMAIN" }; },
  value => { value.adaptation.candidate_mappings[4] = { component_kind: "DOMAIN" }; },
  value => { value.adaptation.owner_review_summary.candidate_mapping_set_digest = "sha256:other-candidate"; },
  value => { value.adaptation.owner_review_summary.digest = "sha256:other-summary"; },
]) {
  const h = harness(); await ready(); h.acknowledge();
  assert.equal(h.byId("oac-primary-action").disabled, false);
  change(h.state.payload); await h.refresh();
  assert.equal(h.byId("oac-review-acknowledgements").hidden, true);
  assert.ok(ackIds.every(id => !h.byId(id).checked));
  assert.equal(h.byId("oac-primary-action").disabled, true);
  assert.doesNotMatch(h.byId("oac-product-validation").textContent, /PASSED/);
  // The command handler must also reject stale programmatic checkbox state.
  h.acknowledge(); await h.click();
  assert.equal(h.requests.filter(request => request.method === "POST").length, 0);
}
""",
    "provider_failure_is_not_an_enterprise_fact_gap": r"""
for (const reason of ["VERTEX_HTTP_ERROR:429", "VERTEX_HTTP_ERROR:503", "/private/secret?token=not-for-display"]) {
 const payload=prepared();payload.status="AGENT_MAPPING_HOLD";
 payload.adaptation.candidate_digest=null;payload.adaptation.candidate_mappings=[];payload.adaptation.owner_review_summary={};
 payload.agent_mapping={status:"HOLD",model_status:"PROVIDER_ERROR",validation_reason_codes:[reason]};
 const h=harness(payload);await ready();
 assert.equal(h.byId("oac-product-gap").textContent,"Enterprise fact gaps have not been assessed");
 assert.equal(h.byId("oac-product-validation").textContent,"No admissible candidate was produced in this attempt");
 assert.match(h.byId("oac-control-note").textContent,/preserve the failed record.*new workspace.*Refresh only reads/);
 assert.equal(h.byId("oac-primary-action").disabled,true);
 const visible=[...['oac-adaptation-status','oac-product-agent','oac-product-gap','oac-control-note']].map(id=>h.byId(id).textContent).join(' ');
 assert.doesNotMatch(visible,/private|token=|not-for-display/);
 if(reason.endsWith('429'))assert.match(visible,/rate limited/);
 await h.refresh();assert.equal(h.requests.filter(r=>r.method==='POST').length,0);
}
""",
    "hold_never_becomes_zero_gaps_or_pass": r"""
for (const mappings of [[], prepared().adaptation.candidate_mappings]) {
  const payload = prepared(); payload.status = "AGENT_MAPPING_HOLD";
  payload.adaptation.candidate_mappings = mappings;
  const h = harness(payload); await ready();
  assert.doesNotMatch(h.byId("oac-product-validation").textContent, /PASSED/);
  assert.notEqual(h.byId("oac-product-gap").textContent, "0 gaps");
  assert.equal(h.byId("oac-primary-action").disabled, true);
}
const payload = prepared(); payload.status = "AGENT_MAPPING_HOLD";
payload.adaptation.gaps = [{ reason_code: "MISSING_OWNER" }];
const h = harness(payload); await ready();
assert.equal(h.byId("oac-product-gap").textContent, "1 gaps");
assert.match(h.byId("oac-fact-gap-detail").textContent, /MISSING_OWNER/);
""",
    "missing_model_receipt_is_not_relabelled_as_keyless_compilation": r"""
const payload = prepared(); payload.status = "AGENT_MAPPING_HOLD";
payload.execution_policy.mapping_source = "INCOMPLETE_MAPPING_EVIDENCE";
payload.execution_policy.mapping_model_provider = null;
payload.agent_mapping = { status: "EVIDENCE_INCOMPLETE", validation_reason_codes: ["MAPPING_EVIDENCE_INCOMPLETE"] };
const h = harness(payload); await ready();
assert.equal(h.byId("oac-product-agent").textContent, "Mapping evidence incomplete");
assert.match(h.byId("oac-product-agent-detail").textContent, /source cannot yet be verified/);
assert.doesNotMatch(h.byId("oac-product-agent-detail").textContent, /no mapping-model call/);
assert.doesNotMatch(h.byId("oac-product-validation").textContent, /PASSED/);
assert.equal(h.byId("oac-primary-action").disabled, true);
""",
    "successful_prepare_with_failed_refresh_remains_visible_and_retryable": r"""
const initial = prepared(); initial.status = "NOT_STARTED"; initial.adaptation = { status: "PACK_OBSERVED" };
initial.execution_policy.available_actions = ["STRUCTURED_PREPARE"];
const h = harness(initial); await ready();
h.state.postResponse = prepared().adaptation;
h.state.failGet = true;
await h.click();
assert.match(h.byId("oac-adaptation-status").textContent, /503/);
assert.equal(h.requests.filter(request => request.method === "POST").length, 1);
assert.equal(h.events.length, 1, "a failed composite read must not announce a new authoritative state");
h.state.payload = prepared(); h.state.failGet = false;
await h.refresh();
assert.doesNotMatch(h.byId("oac-adaptation-status").textContent, /503/);
assert.equal(h.byId("oac-primary-action").dataset.action, "approve");
assert.equal(h.byId("oac-primary-action").disabled, true);
assert.equal(h.requests.filter(request => request.method === "POST").length, 1, "retrying the view must not rerun preparation");
""",
    "duplicate_refreshes_share_one_read_and_restore_bilingual_controls": r"""
const h = harness(); await ready();
const read = h.deferGet(), pending = h.refresh();
for (let i = 0; i < 4; ++i) assert.equal(h.refresh(), pending);
assert.equal(h.requests.length, 2);
assert.equal(h.byId("oac-refresh").textContent, "Reading…");
assert.equal(h.byId("oac-refresh").disabled, true);
assert.equal(h.byId("oac-refresh")["aria-busy"], "true");
h.emit("orgrebase:languagechange", { language: "zh-CN" });
assert.equal(h.byId("oac-refresh").textContent, "读取中…");
read.resolve(response({}, 503)); await pending;
assert.match(h.byId("oac-adaptation-status").textContent, /503/);
assert.equal(h.byId("oac-refresh").disabled, false);
assert.equal(h.byId("oac-refresh").textContent, "刷新适配状态");
await h.refresh();
assert.doesNotMatch(h.byId("oac-adaptation-status").textContent, /503/);
assert.equal(h.byId("oac-refresh")["aria-busy"], "false");
assert.equal(h.requests.filter(request => request.method === "POST").length, 0);
""",
    "pre_command_read_cannot_roll_back_admission_or_replace_new_read_errors": r"""
for (const [oldStatus, newStatus] of [[200, 200], [503, 200], [200, 503]]) {
  const h = harness(); await ready(); h.acknowledge();
  const oldRead = h.deferGet(), oldPending = h.refresh();
  const command = h.deferPost(), postRead = h.deferGet(), action = h.click();
  assert.equal(h.byId("oac-refresh").disabled, true);
  await h.refresh();
  assert.equal(h.requests.filter(request => request.method === "GET").length, 2,
    "manual refresh cannot start another read while the command is pending");
  command.resolve(response(admitted().adaptation)); await ready();
  assert.equal(h.requests.filter(request => request.method === "GET").length, 3,
    "the command must start a fresh read without waiting for the pre-command read");
  postRead.resolve(response(admitted(), newStatus)); await action;
  assert.equal(h.byId("oac-adaptation").dataset.state, "READY_FOR_SHADOW");
  const eventCount = h.events.length, visibleStatus = h.byId("oac-adaptation-status").textContent;
  oldRead.resolve(response(prepared(), oldStatus)); await oldPending;
  assert.equal(h.byId("oac-adaptation").dataset.state, "READY_FOR_SHADOW");
  assert.equal(h.byId("oac-adaptation-status").textContent, visibleStatus);
  assert.equal(h.events.length, eventCount, "stale reads must not broadcast authority changes");
  assert.equal(h.byId("oac-refresh").disabled, false);
  if (newStatus === 503) assert.match(visibleStatus, /503/);
  else assert.doesNotMatch(visibleStatus, /503/);
}
""",
    "workspace_notifications_replace_pending_reads_without_stale_finally_unlock": r"""
for (const oldStatus of [200, 503]) {
  const h = harness(); await ready();
  const oldRead = h.deferGet(), oldPending = h.refresh(), newRead = h.deferGet();
  h.emit("orgrebase:workspacechange");
  const newPending = h.refresh();
  assert.equal(h.requests.length, 3, "workspace notification must not coalesce into the old snapshot");
  oldRead.resolve(response(prepared(), oldStatus)); await oldPending;
  assert.equal(h.byId("oac-refresh").disabled, true);
  assert.equal(h.byId("oac-refresh").textContent, "Reading…");
  assert.equal(h.refresh(), newPending, "old finally must not clear the new pending read");
  assert.equal(h.events.length, 1);
  newRead.resolve(response(admitted())); await newPending;
  assert.equal(h.byId("oac-adaptation").dataset.state, "READY_FOR_SHADOW");
  assert.equal(h.events.length, 2);
  assert.equal(h.byId("oac-refresh").disabled, false);
}
""",
    "workspace_notification_during_post_command_read_gets_a_fresh_tail": r"""
const h = harness(); await ready(); h.acknowledge();
h.state.postResponse = admitted().adaptation;
const postRead = h.deferGet(), action = h.click(); await ready();
const tailRead = h.deferGet();
h.emit("orgrebase:workspacechange");
assert.equal(h.requests.filter(request => request.method === "GET").length, 2);
postRead.resolve(response(prepared())); await ready();
assert.equal(h.requests.filter(request => request.method === "GET").length, 3);
assert.equal(h.byId("oac-refresh").disabled, true);
assert.equal(h.events.length, 1, "the superseded post-command read must not broadcast");
tailRead.resolve(response(admitted())); await action;
assert.equal(h.byId("oac-adaptation").dataset.state, "READY_FOR_SHADOW");
assert.equal(h.byId("oac-adaptation").dataset.gateState, "READY_TO_FORM");
assert.equal(h.events.length, 2);
assert.equal(h.byId("oac-refresh").disabled, false);
""",
    "workspace_notification_during_shadow_command_is_read_after_completion": r"""
const h = harness(admitted()); await ready();
const oldRead = h.deferGet(), oldPending = h.refresh(), command = h.deferPost();
const action = h.enhanced();
oldRead.resolve(response(prepared())); await oldPending;
assert.equal(h.byId("oac-refresh").disabled, true, "old finally cannot unlock a pending command");
const postRead = h.deferGet();
h.emit("orgrebase:workspacechange");
assert.equal(h.requests.filter(request => request.method === "GET").length, 2);
const completed = admitted(); completed.status = completed.adaptation.status = "SHADOW_COMPLETED";
command.resolve(response(completed.adaptation)); await ready();
assert.equal(h.requests.filter(request => request.method === "GET").length, 3);
postRead.resolve(response(completed)); await action;
assert.equal(h.byId("oac-adaptation").dataset.state, "SHADOW_COMPLETED");
assert.equal(h.events.length, 2);
assert.equal(h.byId("oac-refresh").disabled, false);
""",
    "integrity_wrapper_uses_only_allowlisted_public_reasons": r"""
for (const [detail, expected, absent] of [
  [{code: "EVIDENCE_INTEGRITY_FAILED", message: "QUOTE_PARITY_GOLDEN_SUMMARY_UNREADABLE"}, /administrator.*installation.*deployment/i, /Refresh the adaptation state/],
  [{code: "EVIDENCE_INTEGRITY_FAILED", message: "OAC_ADAPTATION_CANDIDATE_DIGEST_MISMATCH"}, /review/i, /installation verification/],
  [{code: "EVIDENCE_INTEGRITY_FAILED", message: "/private/customer/secrets token=do-not-display"}, /EVIDENCE_INTEGRITY_FAILED/, /private|customer|secrets|do-not-display/],
  [{code: "OTHER_WRAPPER", message: "QUOTE_PARITY_GOLDEN_SUMMARY_UNREADABLE"}, /OTHER_WRAPPER/, /installation verification|QUOTE_PARITY_GOLDEN/],
  [{code: "/private/secret-token", message: "do-not-display"}, /request failed/i, /private|secret|do-not-display/],
]) {
  const h = harness(); await ready(); h.acknowledge();
  const command = h.deferPost(), pending = h.click();
  command.resolve({ok: false, status: 409, json: async () => ({detail})}); await pending;
  const rendered = h.byId("oac-adaptation-status").textContent;
  assert.match(rendered, expected); assert.doesNotMatch(rendered, absent);
  assert.equal(h.requests.filter(r => r.method === "POST").length, 1, "failed approval is not replayed");
}
""",
    "invalidated_request_context_is_silent_and_retryable": r"""
for (const phase of ["fetch", "body"]) {
  const h = harness(); await ready();
  const read = h.deferGet(), pending = h.refresh();
  if (phase === "fetch") read.reject(contextChanged());
  else read.resolve({ ok: true, status: 200, json: async () => { throw contextChanged(); } });
  await pending;
  assert.equal(h.events.length, 1);
  assert.doesNotMatch(h.byId("oac-adaptation-status").textContent, /WORKSPACE_REQUEST_CONTEXT_CHANGED|OFFLINE/);
  assert.equal(h.byId("oac-refresh").disabled, false);
  await h.refresh();
  assert.equal(h.events.length, 2);
}
for (const action of ["approve", "shadow"]) {
  const h = harness(action === "approve" ? prepared() : admitted()); await ready(); h.acknowledge();
  const command = h.deferPost(), pending = action === "approve" ? h.click() : h.enhanced();
  command.reject(contextChanged()); await pending;
  assert.equal(h.requests.length, 2, "context invalidation must not trigger a fallback read");
  assert.equal(h.events.length, 1);
  assert.doesNotMatch(h.byId("oac-adaptation-status").textContent, /WORKSPACE_REQUEST_CONTEXT_CHANGED|OFFLINE/);
  assert.equal(h.byId("oac-refresh").disabled, false);
  await h.refresh();
  assert.equal(h.events.length, 2);
}
""",
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for OAC rendering")
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_oac_adaptation_context_projection(scenario: str) -> None:
    result = subprocess.run(
        [shutil.which("node") or "node", "-e", HARNESS
         + '\nconst timeout = setTimeout(() => { console.error("Scenario did not settle"); process.exit(1); }, 5000);'
         + "\n(async () => {\n" + SCENARIOS[scenario]
         + "\n})().then(() => clearTimeout(timeout)).catch(error => { clearTimeout(timeout); console.error(error); process.exitCode = 1; });"],
        cwd=ROOT, capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
