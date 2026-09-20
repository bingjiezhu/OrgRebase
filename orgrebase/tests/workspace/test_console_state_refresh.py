"""Exercise the production state event bridge without browser layout dependencies."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for console events")
def test_workspace_commands_refresh_every_view_and_discard_obsolete_responses():
    script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const app = fs.readFileSync("demo/console/app.js", "utf8");
const shell = fs.readFileSync("demo/console/workspace-shell.js", "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  const listeners = new Map(), reads = [], renders = [], shellRenders = [];
  const window = {
    addEventListener(name, callback) { listeners.set(name, [...(listeners.get(name) || []), callback]); },
    dispatchEvent(event) { for (const listener of listeners.get(event.type) || []) listener(event); },
    OrgRebaseClient: { session: () => ({ mode: "local", principal: { actor_id: "owner", tenant_id: "tenant" } }), workspace: () => "workspace" },
  };
  const CustomEvent = class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } };
  const api = () => new Promise((resolve, reject) => reads.push({ resolve, reject }));
  window.OrgRebaseClient.fetch = async () => ({ ok: true, json: () => api() });
  const context = vm.createContext({ window, CustomEvent, api,
    currentState: null, currentOacAdaptationProof: {}, currentSkillCatalog: null,
    invalidateSkillSource() {}, loadSkillSourceCatalog() {},
    renderCurrentProofOverview() {},
    render(state) { context.currentState = state; renders.push(state); window.dispatchEvent(new CustomEvent("orgrebase:staterendered", { detail: state })); },
    renderWorkspaceUnavailable(options = {}) { context.currentState = null; window.dispatchEvent(new CustomEvent("orgrebase:stateunavailable", { detail: { reason: options.reason } })); },
  });
  const start = app.includes("let workspaceStateRevision") ? app.indexOf("let workspaceStateRevision") : app.indexOf("async function refreshState()");
  vm.runInContext(app.slice(start, app.indexOf("function openOacWorkspaceGate()", start)), context);
  const shellContext = vm.createContext({ window, currentState: null, runProgressFollowing: false,
    taskCandidate:null,taskApproval:null,taskIntakeError:null,byId:()=>null,clearTaskWorkDescription() {},
    renderState(state) { shellContext.currentState = state; shellRenders.push(state); },
    refreshRunProgress() {},
  });
  if (shell.includes("  async function refreshWorkspaceState()")) {
    vm.runInContext(shell.slice(shell.indexOf("  async function refreshWorkspaceState()"), shell.indexOf("  function boot()")), shellContext);
  }
  const shellStart = shell.indexOf('    window.addEventListener("orgrebase:staterendered"');
  vm.runInContext(shell.slice(shellStart, shell.indexOf("\n  }\n\n  window.OrgRebaseWorkspaceShell", shellStart)), shellContext);
  return { context, shellContext, reads, renders, shellRenders,
    emit(name, detail) { window.dispatchEvent(new CustomEvent(name, { detail })); },
    restorePage() { window.dispatchEvent({ type: "pageshow", persisted: true }); } };
}
(async () => {
  const h = harness();
  await tick();
  // Ignore any old shell-only boot request to isolate a command success.
  for (const read of h.reads.splice(0)) read.resolve({ stage: "CURRENT", quote: "v1" });
  await tick(); h.renders.length = h.shellRenders.length = 0;
  h.emit("orgrebase:workspacechange");
  await tick();
  assert.equal(h.reads.length, 1, "one workspace read per completed command");
  const applied = { stage: "CURRENT", quote: "v2", impact: "v2", approval: "v2", evidence: "v2" };
  h.reads.shift().resolve(applied); await tick();
  assert.equal(h.context.currentState, applied, "the main business view must advance after APPLY");
  assert.equal(h.shellContext.currentState, applied, "the shell must receive the same projection");
  assert.equal(h.renders.length, 1); assert.equal(h.shellRenders.length, 1);

  // Two notifications in one turn need one request, while a later mutation
  // during I/O must cause a fresh read and prevent the earlier result rendering.
  h.emit("orgrebase:workspacechange"); h.emit("orgrebase:oacadaptationchange", {});
  await tick(); assert.equal(h.reads.length, 1);
  h.emit("orgrebase:workspacechange");
  h.reads.shift().resolve({ stage: "CURRENT", quote: "obsolete" }); await tick();
  assert.equal(h.context.currentState, applied);
  assert.equal(h.reads.length, 1);
  const latest = { stage: "CURRENT", quote: "v3" };
  h.reads.shift().resolve(latest); await tick();
  assert.equal(h.context.currentState, latest);
  assert.equal(h.shellContext.currentState, latest);

  h.emit("orgrebase:workspacechange"); await tick();
  h.emit("orgrebase:sessionended", { reason: "expired" });
  h.reads.shift().resolve({ stage: "CURRENT", quote: "private old response" }); await tick();
  assert.equal(h.context.currentState, null, "expired sessions must not be resurrected by an old read");

  // An identity change can admit a new read before the old transport settles.
  h.emit("orgrebase:workspacechange"); await tick();
  const old = h.reads.shift();
  h.emit("orgrebase:sessionchange", { mode: "local", principal: { actor_id: "another-owner", tenant_id: "another-tenant" } });
  h.emit("orgrebase:workspacechange"); await tick();
  h.reads.shift().resolve(latest); await tick();
  old.reject(new Error("old session request failed")); await tick();
  assert.equal(h.context.currentState, latest);
  assert.equal(h.shellContext.currentState, latest);

  h.emit("orgrebase:workspacechange"); await tick();
  h.reads.shift().reject(new Error("current refresh failed")); await tick();
  assert.equal(h.context.currentState, null, "a failed command refresh cannot present the old state as current");
  assert.equal(h.shellContext.currentState, null);

  h.emit("orgrebase:workspacechange"); await tick();
  h.emit("pagehide");
  h.reads.shift().resolve(latest); await tick();
  assert.equal(h.context.currentState, null, "workspace navigation must discard old responses");
  h.restorePage(); await tick();
  h.reads.shift().resolve(latest); await tick();
  assert.equal(h.context.currentState, latest, "a restored browser page obtains a fresh projection");
})();
'''
    result = subprocess.run([shutil.which("node") or "node", "-e", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
