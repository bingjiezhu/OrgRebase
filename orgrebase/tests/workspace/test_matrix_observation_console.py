"""Exercise actionable Element failures through the production controls and copy."""

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
const source = fs.readFileSync("demo/console/workspace-shell.js", "utf8");
const copyStart = source.indexOf("  const COPY = Object.freeze({");
const copyEnd = source.indexOf("  const byId =", copyStart);
const controlsStart = source.indexOf("  let elementObservationBusy = false;");
const controlsEnd = source.indexOf("  let runProgressFollowing = false;", controlsStart);
const apiStart = source.indexOf("  function taskIntakeHeaders(");
const apiEnd = source.indexOf("  async function loadTaskWorkDescription(", apiStart);
assert.ok(copyStart >= 0 && copyEnd > copyStart);
assert.ok(controlsStart >= 0 && controlsEnd > controlsStart);
assert.ok(apiStart >= 0 && apiEnd > apiStart);

function state(observation = {}) {
  return {
    actor_id: "actor:employee", stage: "PREVIEWED", business_complete: false,
    quote: { version: "v2", digest: "unchanged" }, approvals: [],
    agentteams_operations: {
      formation_taskflow: { run_id: "run:current" },
      element_observation: {
        workspace_id: "org:one/default", run_id: "run:current", status: "NOT_PUBLISHED",
        ...observation,
      },
    },
  };
}

function observed() {
  return {
    status: "OBSERVED", workspace_id: "org:one/default", run_id: "run:current",
    element_room_url: "https://element.example/#/room/%21private%3Aexample",
    snapshot: { quote_revision: 2 }, observed_at: "2026-09-13T08:00:00Z",
    canonical_writes: 0,
  };
}

function harness(language, initial = state()) {
  const requests = [], events = [];
  const nodes = new Map(["status", "sync", "open"].map(name => [
    "element-observation-" + name,
    { hidden: false, disabled: false, textContent: "", removeAttribute(name) { delete this[name]; } },
  ]));
  const context = vm.createContext({
    URL, language, currentState: initial,
    byId: id => nodes.get(id), taskScope: value => ({ actorId: value.actor_id }),
    format: (label, values) => Object.entries(values).reduce(
      (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)), label,
    ),
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: {
      dispatchEvent(event) { events.push(event.type); },
      OrgRebaseClient: {
        json: (path, options) => new Promise((resolve, reject) => {
          requests.push({ path, options, resolve, reject });
        }),
      },
    },
  });
  vm.runInContext(source.slice(copyStart, copyEnd)
    + "\nfunction copy() { return COPY[language]; }\n"
    + source.slice(apiStart, apiEnd) + source.slice(controlsStart, controlsEnd), context);
  return {
    context, requests, events, copy: () => context.copy(),
    button: nodes.get("element-observation-sync"), link: nodes.get("element-observation-open"),
    status: nodes.get("element-observation-status"),
    publish: () => context.syncElementObservation(),
    render: () => context.renderElementObservation(context.currentState),
  };
}

function failure(code) {
  return Object.assign(new Error("untrusted response: secret-token=<private>"), {
    code, detail: { message: "secret-token=<private>" },
  });
}
"""

SCENARIOS = {
    "persisted_error_categories": r"""
  const h = harness(language);
  const groups = {
    elementConfigFailed: ["MATRIX_OBSERVATION_NOT_CONFIGURED", "MATRIX_OBSERVATION_CONFIG_INVALID",
      "MATRIX_OBSERVATION_URL_INVALID", "MATRIX_OBSERVATION_WORKSPACE_URL_REQUIRED",
      "MATRIX_OBSERVATION_WORKSPACE_SCOPE_INVALID", "MATRIX_OBSERVATION_TOKEN_MISSING",
      "MATRIX_OBSERVATION_IDENTITY_INVALID"],
    elementConfigPrivate: ["MATRIX_OBSERVATION_CONFIG_NOT_PRIVATE"],
    elementSignInRequired: ["AUTH_SESSION_REQUIRED", "AUTH_BEARER_REQUIRED", "AUTH_TOKEN_INVALID",
      "AUTH_TOKEN_EXPIRED", "HTTP_401"],
    elementPermissionDenied: ["AUTH_ACTION_DENIED", "AUTH_MEMBERSHIP_DENIED", "AUTH_TENANT_DENIED",
      "MATRIX_OBSERVATION_TASK_ACTOR_REQUIRED", "HTTP_403"],
    elementPublisherCredentials: ["MATRIX_OBSERVATION_HTTP_401", "MATRIX_OBSERVATION_SENDER_MISMATCH",
      "MATRIX_OBSERVATION_PUBLISHER_IS_GUEST"],
    elementRoomPermissions: ["MATRIX_OBSERVATION_HTTP_403", "MATRIX_OBSERVATION_PUBLISHER_NOT_JOINED",
      "MATRIX_OBSERVATION_VIEWER_NOT_INVITED", "MATRIX_OBSERVATION_ROOM_PERMISSIONS_INVALID"],
    elementWorkspaceDenied: ["MATRIX_OBSERVATION_WORKSPACE_FORBIDDEN"],
    elementConnectionFailed: ["MATRIX_OBSERVATION_NETWORK_FAILED", "MATRIX_OBSERVATION_HTTP_408",
      "MATRIX_OBSERVATION_HTTP_429", "MATRIX_OBSERVATION_HTTP_500", "MATRIX_OBSERVATION_HTTP_503",
      "HTTP_408", "HTTP_429", "HTTP_502"],
  };
  const messages = Object.keys(groups).map(key => h.copy()[key]);
  assert.equal(new Set(messages).size, messages.length, "different remedies must remain distinguishable");
  for (const [key, codes] of Object.entries(groups)) {
    assert.match(h.copy()[key], language === "en" ? /retry/i : /重试/);
    for (const code of codes) {
      h.context.currentState = state({ status: "UNAVAILABLE", error_code: code });
      h.render();
      assert.equal(h.status.textContent, h.copy()[key], code);
      assert.equal(h.button.hidden, false);
      assert.equal(h.button.disabled, false);
      assert.equal(h.link.hidden, true);
    }
  }
""",
    "request_failure_retry_and_business_boundary": r"""
  const h = harness(language, state(observed()));
  const before = JSON.stringify(h.context.currentState);
  const pending = h.publish();
  assert.equal(h.button.disabled, true);
  h.requests[0].reject(failure("AUTH_ACTION_DENIED"));
  await pending;
  assert.ok(h.status.textContent.endsWith(h.copy().elementPermissionDenied));
  assert.ok(!h.status.textContent.includes("secret-token"));
  assert.equal(JSON.stringify(h.context.currentState), before, "sync cannot approve or apply a business change");
  assert.equal(h.button.disabled, false);
  assert.equal(h.link.hidden, false, "the previous verified room remains accessible after denied publication");

  // A fresh permission failure must remain actionable even over an older persisted network failure.
  h.context.currentState = state({ status: "UNAVAILABLE", error_code: "MATRIX_OBSERVATION_NETWORK_FAILED" });
  h.render();
  assert.equal(h.status.textContent, h.copy().elementPermissionDenied);
  const prior = JSON.stringify(h.context.currentState);
  const retry = h.publish();
  assert.equal(h.status.textContent, h.copy().elementPublishing);
  assert.equal(h.requests.length, 2);
  for (const request of h.requests) {
    assert.equal(request.path, "/api/workspace/agentteams-observation");
    assert.equal(request.options.method, "POST");
    assert.equal(request.options.headers["X-OrgRebase-Actor"], "actor:employee");
    assert.equal(JSON.stringify(request.options.body), JSON.stringify({ actor_id: "actor:employee", run_id: "run:current" }));
  }
  h.requests[1].resolve({ ...observed(), business_complete: true, approvals: ["untrusted"] });
  await retry;
  assert.equal(JSON.stringify(h.context.currentState), prior, "POST response cannot replace canonical workspace state");
  assert.equal(h.link.hidden, true, "success must await the trusted state refresh");
  assert.equal(h.events.join(","), "orgrebase:workspacechange,orgrebase:workspacechange");
  h.context.currentState = state(observed());
  h.render();
  assert.equal(h.link.hidden, false);
  assert.ok(h.status.textContent.startsWith(language === "en" ? "Synced through quote v2" : "已同步至报价 v2"));
  assert.equal(h.context.currentState.business_complete, false);
  assert.equal(h.context.currentState.approvals.length, 0);
  assert.match(h.copy().elementBoundary, language === "en" ? /Approvals remain in the workspace/ : /审批仍在工作台完成/);
""",
    "unknown_codes_and_remote_details_stay_private": r"""
  const h = harness(language);
  for (const code of [undefined, null, "MATRIX_OBSERVATION_NEW_FAILURE", "MATRIX_OBSERVATION_HTTP_401 token=secret-token",
    "<script>secret-token</script>", { message: "secret-token" }, ["HTTP_503"]]) {
    h.context.currentState = state({ status: "UNAVAILABLE", error_code: code });
    h.render();
    assert.equal(h.status.textContent, h.copy().elementFailed);
    h.context.currentState = state();
    const pending = h.publish();
    h.requests.at(-1).reject(failure(code));
    await pending;
    assert.equal(h.status.textContent, h.copy().elementReady + " · " + h.copy().elementRequestFailed);
    assert.ok(!h.status.textContent.includes("secret-token"));
    assert.equal(h.button.disabled, false);
  }
""",
    "request_error_code_and_scope": r"""
  const h = harness(language);
  const pending = h.publish();
  h.requests[0].reject({ error_code: "MATRIX_OBSERVATION_CONFIG_NOT_PRIVATE", message: "secret-token" });
  await pending;
  assert.ok(h.status.textContent.endsWith(h.copy().elementConfigPrivate));
  h.context.language = language === "en" ? "zh-CN" : "en";
  h.render();
  assert.ok(h.status.textContent.endsWith(h.copy().elementConfigPrivate), "failure copy follows the selected language");
  h.context.currentState = state();
  h.context.currentState.agentteams_operations.element_observation.workspace_id = "org:another/default";
  h.render();
  assert.equal(h.status.textContent, h.copy().elementReady, "an error cannot follow another workspace");
""",
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for console controls")
@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_element_observation_actionable_errors(language: str, scenario: str) -> None:
    program = HARNESS + f'\nconst language = "{language}";\n(async () => {{\n'
    program += SCENARIOS[scenario] + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n"
    result = subprocess.run(
        [shutil.which("node") or "node", "-e", program],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
