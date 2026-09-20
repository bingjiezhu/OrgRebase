"""Exercise the production Element controls with delayed transport responses."""

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
const start = source.indexOf("  let elementObservationBusy = false;");
const end = source.indexOf("  let runProgressFollowing = false;", start);
assert.ok(start >= 0 && end > start, "production observation controls must exist");

function state(workspaceId, observation = {}) {
  return {
    actor_id: "actor:employee",
    agentteams_operations: {
      formation_taskflow: { run_id: "run:same-name" },
      element_observation: {
        workspace_id: workspaceId, run_id: "run:same-name", status: "NOT_PUBLISHED", ...observation,
      },
    },
  };
}

function observed(workspaceId, url = "https://element.example/#/room/%21private%3Aexample") {
  return {
    status: "OBSERVED", workspace_id: workspaceId, run_id: "run:same-name",
    element_room_url: url, snapshot: { quote_revision: 3 }, observed_at: "2026-09-13T08:00:00Z",
  };
}

function harness(initialState = state("org:one/default")) {
  const requests = [], events = [];
  const nodes = new Map(["status", "sync", "open"].map(name => [
    "element-observation-" + name,
    { hidden: false, disabled: false, textContent: "", removeAttribute(name) { delete this[name]; } },
  ]));
  const context = vm.createContext({
    URL, language: "en", currentState: initialState,
    byId: id => nodes.get(id), taskScope: value => ({ actorId: value.actor_id }),
    copy: () => ({
      elementPublishing: "publishing", elementUnavailable: "not configured", elementWaiting: "waiting",
      elementFailed: "failed", elementStale: "stale", elementReady: "ready", elementObserved: "observed",
      elementRequestFailed: "request failed",
    }),
    format: (label, values) => label + " " + JSON.stringify(values),
    taskIntakeApi: (path, payload, actor) => new Promise((resolve, reject) => {
      requests.push({ path, payload, actor, resolve, reject });
    }),
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { dispatchEvent(event) { events.push(event.type); } },
  });
  vm.runInContext(source.slice(start, end), context);
  return {
    context, requests, events,
    button: nodes.get("element-observation-sync"), link: nodes.get("element-observation-open"),
    status: nodes.get("element-observation-status"),
    publish: () => context.syncElementObservation(),
    render: () => context.renderElementObservation(context.currentState),
    observation: () => context.currentState.agentteams_operations.element_observation,
  };
}
"""

SCENARIOS = {
    "same_run_cross_workspace_response": r"""
  const h = harness();
  const pending = h.publish();
  assert.equal(h.requests.length, 1);
  assert.equal(h.button.disabled, true);
  h.context.currentState = state("org:another/default");
  const currentObservation = h.observation();
  h.requests[0].resolve(observed("org:one/default"));
  await pending;
  assert.equal(h.observation(), currentObservation, "a same-named run in another workspace must not receive this result");
  assert.equal(h.events.length, 0);
  assert.equal(h.link.hidden, true);
  assert.equal(h.button.disabled, false);

  const anotherPending = h.publish();
  h.context.currentState = state("org:third/default");
  h.requests[1].reject(new Error("previous workspace request failed"));
  await anotherPending;
  assert.equal(h.observation().status, "NOT_PUBLISHED", "an old failure must not poison the next workspace");
  assert.equal(h.observation().workspace_id, "org:third/default");

  const wrongResponse = h.publish();
  h.requests[2].resolve(observed("org:unrelated/default"));
  await wrongResponse;
  assert.equal(h.observation().status, "NOT_PUBLISHED", "response scope must match even without navigation");
  assert.equal(h.events.length, 1, "a current request reloads trusted state without adopting its response");
""",
    "failure_preserves_retry_scope": r"""
  const h = harness();
  const failed = h.publish();
  h.requests[0].reject(new Error("Matrix unavailable"));
  await failed;
  assert.equal(h.observation().status, "NOT_PUBLISHED", "a transport error is not a durable observation receipt");
  assert.equal(h.observation().workspace_id, "org:one/default");
  assert.equal(h.observation().run_id, "run:same-name");
  assert.equal(h.button.hidden, false);
  assert.equal(h.button.disabled, false);
  assert.equal(h.link.hidden, true);
  assert.equal(h.events.join(","), "orgrebase:workspacechange");
  h.context.currentState = state("org:one/default", { status: "UNAVAILABLE" });
  h.render();
  assert.equal(h.status.textContent, "failed", "the persisted failure arrives through the state refresh");

  const retry = h.publish();
  assert.equal(h.requests.length, 2, "a failed request must not disable the next attempt");
  assert.equal(h.requests[1].path, "/api/workspace/agentteams-observation");
  assert.equal(h.requests[1].payload.run_id, "run:same-name");
  assert.equal(h.requests[1].payload.actor_id, "actor:employee");
  assert.equal(Object.keys(h.requests[1].payload).sort().join(","), "actor_id,run_id");
  h.requests[1].resolve(observed("org:one/default"));
  await retry;
  assert.equal(h.observation().status, "UNAVAILABLE", "success also waits for the canonical state refresh");
  assert.equal(h.link.hidden, true);
  assert.equal(h.events.join(","), "orgrebase:workspacechange,orgrebase:workspacechange");
  h.context.currentState = state("org:one/default", observed("org:one/default"));
  h.render();
  assert.equal(h.link.hidden, false);
""",
    "late_same_run_response_preserves_newer_state": r"""
  for (const failed of [false, true]) {
    const h = harness(state("org:one/default", observed("org:one/default")));
    const pending = h.publish();
    const newer = { ...observed("org:one/default"), status: "STALE", snapshot: { quote_revision: 4 } };
    h.context.currentState = state("org:one/default", newer);
    h.render();
    const before = h.observation();
    if (failed) h.requests[0].reject(new Error("old publication failed"));
    else h.requests[0].resolve(observed("org:one/default"));
    await pending;
    assert.equal(h.observation(), before, "late responses must not overwrite a newer same-run projection");
    assert.equal(h.observation().snapshot.quote_revision, 4);
    assert.equal(h.observation().status, "STALE");
    assert.equal(h.link.hidden, true);
    assert.equal(h.events.join(","), "orgrebase:workspacechange");
  }
""",
    "write_denied_read_allowed_keeps_failure_visible": r"""
  for (const prior of [{ status: "NOT_PUBLISHED" }, observed("org:one/default")]) {
    const h = harness(state("org:one/default", prior));
    const pending = h.publish();
    h.requests[0].reject(new Error("AUTH_ACTION_DENIED"));
    await pending;
    h.context.currentState = state("org:one/default", prior);
    h.render();
    assert.equal(h.observation().status, prior.status, "a rejected write does not alter the stored receipt");
    assert.ok(h.status.textContent.includes("request failed"), "a successful GET must not hide the rejected POST");
    assert.equal(h.button.disabled, false);
    const retry = h.publish();
    assert.equal(h.status.textContent, "publishing", "a new attempt clears the previous request error");
    h.requests[1].resolve(observed("org:one/default"));
    await retry;
    assert.ok(!h.status.textContent.includes("request failed"));

    const failed = h.publish();
    h.requests[2].reject(new Error("AUTH_ACTION_DENIED"));
    await failed;
    h.context.currentState = state("org:another/default");
    h.render();
    assert.equal(h.status.textContent, "ready", "request errors must not follow another workspace");
  }
""",
    "stale_observation_clears_old_link": r"""
  const h = harness(state("org:one/default", observed("org:one/default")));
  h.render();
  assert.equal(h.link.hidden, false);
  assert.ok(h.link.href);
  h.context.currentState = state("org:one/default", { ...observed("org:one/default"), status: "STALE" });
  h.render();
  assert.equal(h.link.hidden, true, "stale state must hide a previously visible room link");
  assert.equal("href" in h.link, false, "hiding alone must not retain a clickable stale target");
  assert.equal(h.status.textContent, "stale");
  assert.equal(h.button.hidden, false);
  assert.equal(h.button.disabled, false);
""",
    "room_link_accepts_only_safe_http_schemes": r"""
  const h = harness();
  for (const url of ["http://127.0.0.1:18091/#/room/test", "https://element.example/#/room/test"]) {
    h.context.currentState = state("org:one/default", observed("org:one/default", url));
    h.render();
    assert.equal(h.link.hidden, false, "HTTP(S) room links should remain usable");
    assert.equal(h.link.href, new URL(url).href);
  }
  for (const url of [
    "javascript:alert(1)", "data:text/html,hello", "file:///etc/passwd", "ftp://example.com/room",
    "https://user:secret@element.example/room", "/relative-room", "not a URL",
  ]) {
    h.context.currentState = state("org:one/default", observed("org:one/default", url));
    h.render();
    assert.equal(h.link.hidden, true, "unsafe or ambiguous room URL must not become a link");
    assert.equal("href" in h.link, false);
  }
""",
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for console controls")
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_element_controls_preserve_run_scope_and_safe_retry(scenario: str) -> None:
    program = HARNESS + "\n(async () => {\n" + SCENARIOS[scenario] + "\n})().catch(error => {\n"
    program += "console.error(error); process.exitCode = 1;\n});\n"
    result = subprocess.run(
        [shutil.which("node") or "node", "-e", program],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
