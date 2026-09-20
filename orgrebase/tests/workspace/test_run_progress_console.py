"""Exercise the shipped progress renderer against scoped, partial evidence."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("demo/console/workspace-shell.js", "utf8");
let focusedElement = null;
class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.hidden = false; this.open = false; this.ownText = ""; this.parentNode = null; this.scrollTop = 0; this.scrollLeft = 0; }
  set textContent(value) { this.replaceChildren(); this.ownText = String(value); }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(" "); }
  set innerHTML(_) { throw new Error("Dynamic evidence must not be parsed as markup"); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  addEventListener(type, callback) { (this.listeners[type] ??= []).push(callback); }
  click() { if (!this.disabled) for (const callback of this.listeners.click || []) callback(); }
  scrollIntoView() { this.scrolled = true; }
  append(...nodes) {
    for (const node of nodes.flatMap(item => item.tag === "fragment" ? [...item.children] : [item])) {
      node.remove(); node.parentNode = this; this.children.push(node);
    }
  }
  prepend(node) { node.remove(); node.parentNode = this; this.children.unshift(node); }
  replaceChildren(...nodes) { for (const child of [...this.children]) child.remove(); this.ownText = ""; this.append(...nodes); }
  remove() {
    if (!this.parentNode) return;
    for (let current = focusedElement; current; current = current.parentNode) {
      if (current === this) { focusedElement = null; break; }
    }
    this.parentNode.children = this.parentNode.children.filter(child => child !== this);
    this.parentNode = null;
  }
  focus() { focusedElement = this; }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    const match = selector.match(/^\[([^=]+)="([^"]+)"\]$/);
    return this.children.flatMap(child => [
      ...(selector.startsWith(".") ? (child.className || "").split(" ").includes(selector.slice(1))
        : match && child.attributes[match[1]] === match[2]) ? [child] : [],
      ...child.querySelectorAll(selector),
    ]);
  }
}
const descendants = (element, tag) => element.children.flatMap(child => [
  ...(child.tag === tag ? [child] : []), ...descendants(child, tag),
]);
function harness() {
  const ids = ["observed-run-progress", "observed-run-progress-grid", "observed-run-progress-status",
    "observed-run-progress-id", "observed-run-progress-resume", "observed-run-details",
    "observed-run-details-summary", "observed-run-details-content", "agent-work-observability"];
  const nodes = new Map(ids.map(id => [id, new Element()]));
  const context = vm.createContext({
    language: "en", currentState: { stage: "CURRENT", execution: { run_id: "run:current" } },
    stateAvailability: "ready", runProgressDetailsKey: null,
    STAGES: ["EMPTY", "CURRENT", "PREVIEWED", "APPROVED"], ROUTES: [],
    renderContextFacts() {}, renderElementObservation() {}, renderOverviewFlow() {}, renderMaterials() {},
    renderTaskPrerequisite() {}, renderLifecycleRail() {}, renderTaskIntake() {},
    fetch() { throw new Error("Evidence navigation must not request data"); },
    window: { OrgRebaseClient: { json() { throw new Error("Evidence navigation must not invoke a command or query"); } } },
    document: { getElementById: id => nodes.get(id), createElement: tag => new Element(tag),
      createDocumentFragment: () => new Element("fragment"), get activeElement() { return focusedElement; } },
  });
  const slices = [
    source.slice(source.indexOf("  const COPY ="), source.indexOf("  let language =")),
    source.slice(source.indexOf("  function copy()"), source.indexOf("  function routeFromHash()")),
    source.slice(source.indexOf("  function make("), source.indexOf("  function page(")),
    source.slice(source.indexOf("  function renderRunProgress(progress)"), source.indexOf("  async function refreshRunProgress")),
    source.slice(source.indexOf("  function renderState("), source.indexOf("  function boot()")),
  ];
  vm.runInContext(slices.join("\n"), context);
  return { context, nodes, render: value => context.renderRunProgress(value),
    content: nodes.get("observed-run-details-content"), details: nodes.get("observed-run-details") };
}
function progress(extra = {}) {
  return {
    schema_version: "orgrebase.workspace-run-progress-view.v1", status: "ACTIVE", run_id: "run:current",
    milestones: [], actions: [
      { sequence: 1, tool: "projectflow", action: "create_project", key: "project:create", status: "active" },
      { sequence: 2, tool: "projectflow", action: "plan_dag", key: "project:plan-r1", status: "active" },
      { sequence: 3, tool: "taskflow", action: "ack_task", key: "review:ack", status: "in_progress" },
      { sequence: 4, tool: "projectflow", action: "plan_dag", key: "project:plan-r2", status: "active" },
    ], reviewer_model_usage: [], ...extra,
  };
}
function evidence(h, observedView = null) {
  const output = (task, actor, role, attempt, digit, extra = {}) => ({
    task_id: task, actor_id: actor, role, attempt, output_digest: `sha256:${digit.repeat(64)}`, ...extra,
  });
  const outputs = observedView?.outputs || [
    output("legal-original", "legal-steward", "DOMAIN_WORKER", 1, "1", { domain: "legal" }),
    output("review-original", "independent-reviewer", "REVIEWER", 1, "2", { decision: { verdict: "REPLAN", missing_domains: ["legal"] } }),
    output("legal-recovery", "legal-steward", "DOMAIN_WORKER", 2, "3", { domain: "legal" }),
    output("review-recovery", "independent-reviewer", "REVIEWER", 2, "4", { decision: { verdict: "PASS", missing_domains: [] } }),
    output("unrelated-product", "product-steward", "DOMAIN_WORKER", 1, "5", { domain: "product" }),
  ];
  const view = observedView || { run_id: "run:current", status: "PASS", candidate_only: true, target_writes: 0, outputs };
  h.context.currentState.agent_candidate_outputs = view;
  const panel = h.nodes.get("agent-work-observability");
  panel.dataset.runId = view.run_id;
  const cards = new Map();
  const attempts = new Map();
  outputs.forEach(output => {
    let card = cards.get(output.actor_id);
    if (!card) {
      card = new Element("details"); card.className = "agent-work-card";
      card.dataset.agentActor = output.actor_id;
      cards.set(output.actor_id, card); panel.append(card);
    }
    const attempt = new Element("article"); attempt.className = "agent-work-candidate-attempt";
    attempt.dataset.taskId = output.task_id; attempt.dataset.attempt = String(output.attempt);
    attempt.setAttribute("title", output.output_digest);
    attempts.set(output.task_id, attempt); card.append(attempt);
  });
  return { view, panel, cards, attempts, button: () => descendants(h.content, "button")[0] };
}
"""

SCENARIOS = {
    "replan_navigation_opens_only_bound_review_and_affected_domain_details": r"""
const h = harness();
const e = evidence(h);
h.render(progress());
const button = e.button();
assert.equal(button.textContent, "This run: view evidence recovery and review");
button.click();
assert.equal(e.cards.get("independent-reviewer").open, true);
assert.equal(e.cards.get("legal-steward").open, true, "affected domain comes from the observed review, not a Finance fixture");
assert.equal(e.cards.get("product-steward").open, false, "unrelated domain details stay untouched");
const review = e.attempts.get("review-original");
assert.equal(h.context.document.activeElement, review);
assert.equal(review.scrolled, true);
assert.equal(review.getAttribute("tabindex"), "-1");
h.context.language = "zh-CN";
h.render(progress());
assert.equal(e.button().textContent, "本次运行：查看补证与复核");
""",  # noqa: RUF001 -- Match the Chinese UI label exactly.
    "replan_navigation_requires_verified_projection_and_exact_rendered_bindings": r"""
const mutations = [
  e => { e.view.run_id = "run:other"; },
  e => { e.panel.dataset.runId = "run:other"; },
  e => { e.view.status = "FAIL"; },
  e => { e.view.candidate_only = false; },
  e => { e.view.target_writes = 1; },
  e => { e.panel.hidden = true; },
  e => { e.attempts.get("review-original").dataset.taskId = "another-task"; },
  e => { e.attempts.get("review-original").dataset.attempt = "2"; },
  e => { e.attempts.get("review-original").setAttribute("title", `sha256:${"0".repeat(64)}`); },
  e => { e.attempts.get("legal-recovery").remove(); },
  e => { e.cards.get("legal-steward").dataset.agentActor = "finance-steward"; },
  e => { e.view.outputs[1].decision.verdict = "PASS"; },
];
for (const mutate of mutations) {
  const h = harness(); const e = evidence(h); mutate(e); h.render(progress());
  assert.equal(e.button(), undefined, String(mutate));
  assert([...e.cards.values()].every(card => !card.open));
}
const h = harness(); const e = evidence(h);
h.render(progress({ actions: progress().actions.slice(0, 3) }));
assert.equal(e.button(), undefined, "a review alone does not manufacture an observed replanning action");
h.render(progress({ actions: progress().actions.map(action => ({ ...action, sequence: 0 })) }));
assert.equal(e.button(), undefined);
""",
    "replan_navigation_rechecks_current_evidence_when_clicked_and_after_polling": r"""
const h = harness();
h.render(progress());
const e = evidence(h);
h.render(progress());
assert(e.button(), "same action journal gains navigation only once matching verified outputs arrive");
const oldButton = e.button();
e.view.status = "FAIL";
oldButton.click();
assert.equal(oldButton.disabled, true);
assert([...e.cards.values()].every(card => !card.open));
h.render(progress());
assert.equal(e.button(), undefined, "same journal loses navigation when current evidence is unavailable");
e.view.status = "PASS";
h.render(progress());
const priorRunButton = e.button();
h.context.currentState.execution.run_id = "run:new";
priorRunButton.click();
assert.equal(priorRunButton.disabled, true);
assert([...e.cards.values()].every(card => !card.open), "a stale handler cannot open another run's cards");
""",
    "formation_failure_is_scoped_and_replan_is_only_a_received_result": r"""
const h = harness();
h.render(progress({ status: "FAILED", failure_code: "MODEL_PROVIDER_UNAVAILABLE" }));
const status = h.nodes.get("observed-run-progress-status");
assert(status.textContent.includes("Initial task formation failed: MODEL_PROVIDER_UNAVAILABLE"));
assert(status.textContent.includes("corresponding proposal"));
h.render(progress({ status: "FAILED", failure_code: "private token <script>" }));
assert(!status.textContent.includes("private token"));
assert(status.textContent.includes("Not reported"));
h.render(progress({ status: "RUNNING", failure_code: "REPLAN", milestones: [
  { id: "REVIEWER_ACCEPTED", status: "OBSERVED", observed_count: 1, expected_count: 1 },
] }));
assert(!status.textContent.includes("failed"));
const rail = h.nodes.get("observed-run-progress-grid").textContent;
assert(rail.includes("Review task result received"));
assert(!rail.includes("Reviewer accepted"));
h.context.language = "zh-CN";
h.render(progress({ status: "FAILED", failure_code: "MODEL_PROVIDER_UNAVAILABLE" }));
assert(status.textContent.includes("初始任务形成失败原因"));
""",
    "polling_preserves_detail_nodes_scroll_and_keyboard_focus": r"""
const h = harness();
h.render(progress({ status: "RUNNING" }));
h.details.open = true;
const region = h.content.querySelector('[data-progress-table="actions"]');
const originalBody = descendants(region, "tbody")[0];
region.scrollTop = 160;
region.scrollLeft = 45;
region.focus();
h.render(progress({ status: "RUNNING", updated_at: "a later polling instant" }));
assert.equal(h.content.querySelector('[data-progress-table="actions"]'), region);
assert.equal(descendants(region, "tbody")[0], originalBody, "identical details must not rebuild any table rows");
assert.equal(region.scrollTop, 160);
assert.equal(region.scrollLeft, 45);
assert.equal(h.context.document.activeElement, region);

const updated = progress({ actions: [...progress().actions,
  { sequence: 5, tool: "taskflow", action: "check_task", key: "review:check", status: "observed" }],
  reviewer_model_usage: [{ task_id: "reviewer", phase: 1, provider: "local", model_id: "model",
    input_tokens: 12, output_tokens: null, latency_ms: 230, usage_source: "provider_response", latency_source: "client_receipt" }] });
h.render(updated);
assert.equal(h.content.querySelector('[data-progress-table="actions"]'), region, "new rows must reuse the scrolling region");
assert.equal(descendants(region, "tbody")[0].children.length, 5);
assert.equal(region.scrollTop, 160);
assert.equal(region.scrollLeft, 45);
assert.equal(h.context.document.activeElement, region, "a focused scroll region must not detach during an update");
assert.equal(h.content.children[0], region, "actions remain before model usage when evidence arrives");

h.context.language = "zh-CN";
h.render(updated);
assert.equal(h.content.querySelector('[data-progress-table="actions"]'), region);
assert.equal(descendants(region, "th")[0].textContent, "序号");
assert.equal(h.context.document.activeElement, region);
assert.equal(h.details.open, true);
""",
    "actions_follow_actual_journal_and_keep_details_open": r"""
const h = harness();
h.render(progress());
assert.equal(h.details.hidden, false);
assert.equal(h.nodes.get("observed-run-details-summary").textContent, "Run details · 4 observed actions");
const body = descendants(h.content, "tbody")[0];
assert.equal(body.children.length, 4);
assert.deepEqual(body.children.map(row => Boolean(row.className)), [false, false, false, true]);
assert.ok(body.children[3].textContent.includes("Replanning"), "replanning is an operation, not a fixed row index");
assert.ok(!body.children[1].textContent.includes("Replanning"), "the first plan is not a replan");
assert.equal(descendants(h.content, "th").every(th => th.attributes.scope === "col"), true);
h.details.open = true;
h.render(progress({ actions: progress().actions.slice(0, 3) }));
assert.equal(h.details.open, true, "polling must not close an expanded detail view");
assert.equal(descendants(h.content, "tbody")[0].children.length, 3);
assert.ok(!h.content.textContent.includes("Replanning"));
""",
    "usage_keeps_unknown_separate_from_zero_and_escapes_text": r"""
const h = harness();
const attack = '<img src=x onerror="alert(1)">';
h.render(progress({ actions: [{ sequence: 1, tool: "taskflow", action: attack, key: attack, status: "observed" }],
  reviewer_model_usage: [{ task_id: attack, phase: 1, provider: "local", model_id: attack,
    input_tokens: 0, output_tokens: null, latency_ms: 0, usage_source: "provider_response", latency_source: "client_receipt" }] }));
const rows = descendants(h.content, "tbody")[1].children;
assert.equal(rows[0].children[2].textContent, "0");
assert.equal(rows[0].children[3].textContent, "Not reported");
assert.equal(rows[0].children[4].textContent, "0 ms");
assert.ok(rows[0].children[5].textContent.includes("Provider response"));
assert.ok(rows[0].children[5].textContent.includes("Client call receipt"));
assert.ok(h.content.textContent.includes(attack), "untrusted identifiers remain text");
assert.equal(descendants(h.content, "img").length, 0);
assert.equal(descendants(h.content, "script").length, 0);
""",
    "late_other_run_response_cannot_repopulate_details": r"""
const h = harness();
h.render(progress());
h.context.renderState({ stage: "CURRENT", execution: { run_id: "run:new" } });
assert.equal(h.details.hidden, true, "changing the current run clears its previous evidence immediately");
assert.equal(h.content.textContent, "");
assert.equal(h.render(progress()), null, "a delayed old-run result must not be presented as the new run");
assert.equal(h.details.hidden, true);
assert.equal(h.content.textContent, "");
h.render(progress({ run_id: "run:new" }));
assert.equal(h.details.hidden, false);
h.context.currentState = null;
h.context.renderState(null, { availability: "unavailable" });
assert.equal(h.details.hidden, true, "unavailable or signed-out state clears private detail rows");
assert.equal(h.render(progress({ run_id: "run:new" })), null,
  "a delayed progress response cannot refill details after canonical state becomes unavailable");
assert.equal(h.details.hidden, true);
assert.equal(h.content.textContent, "");
h.context.renderState({ stage: "EMPTY", execution: { run_id: "run:before-formation" } });
assert.equal(h.render(progress({ status: "RUNNING", run_id: "run:new-formation" })), "RUNNING",
  "after a valid EMPTY state returns, a newly allocated Formation run remains observable");
assert.equal(h.details.hidden, false);
""",
    "partial_and_empty_progress_never_fills_missing_evidence": r"""
const h = harness();
h.context.currentState = { stage: "EMPTY", execution: { run_id: "run:before-formation" } };
assert.equal(h.render(progress({ status: "RUNNING", actions: [], reviewer_model_usage: [] })), "RUNNING",
  "a newly allocated Formation run can be observed before the canonical call returns");
assert.equal(descendants(h.content, "tbody").length, 0);
assert.ok(h.content.textContent.includes("No verifiable action journal"));
assert.ok(h.content.textContent.includes("No verified Reviewer model receipts"));
h.render(null);
assert.equal(h.details.hidden, true);
assert.equal(h.content.textContent, "");
""",
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for console rendering")
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_progress_evidence_renderer(scenario: str) -> None:
    result = subprocess.run(
        [shutil.which("node") or "node", "-e", HARNESS + SCENARIOS[scenario]],
        cwd=ROOT, capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for console rendering")
def test_replan_navigation_with_verified_golden_api_projections(tmp_path: Path) -> None:
    from orgrebase.api import _same_run_candidate_output_view, _workspace_run_progress_view

    pilot = ROOT / "evidence" / "golden-competition" / "latest" / "pilot"
    state = json.loads((pilot / "state.json").read_text(encoding="utf-8"))
    (tmp_path / "run-recorded").symlink_to(pilot / "golden-run", target_is_directory=True)
    workspace = SimpleNamespace(competition_evidence_root=tmp_path, state=lambda: state)
    progress_view = _workspace_run_progress_view(workspace)
    candidates = _same_run_candidate_output_view(workspace, state)
    assert candidates["status"] == "PASS"
    assert len(candidates["outputs"]) == 7
    plans = [item for item in progress_view["actions"] if item["action"] == "plan_dag"]
    assert len(plans) == 2
    assert {item["status"] for item in plans} == {"active"}
    payload = {
        "state": {"stage": state["stage"], "execution": state["execution"]},
        "progress": progress_view,
        "candidates": candidates,
    }
    script = r"""
const actual = JSON.parse(fs.readFileSync(0, "utf8"));
const h = harness();
h.context.currentState = actual.state;
const e = evidence(h, actual.candidates);
h.render(actual.progress);
const button = e.button();
assert(button, "the verified real journal's active plan_dag status must expose review navigation");
button.click();
const replan = actual.candidates.outputs.find(output => output.decision?.verdict === "REPLAN");
assert.equal(h.context.document.activeElement, e.attempts.get(replan.task_id));
assert.equal(e.cards.get(replan.actor_id).open, true);
for (const output of actual.candidates.outputs.filter(output => output.role === "DOMAIN_WORKER")) {
  assert.equal(e.cards.get(output.actor_id).open, replan.decision.missing_domains.includes(output.domain));
}
for (const card of e.cards.values()) card.open = false;
e.attempts.get(replan.task_id).setAttribute("title", `sha256:${"0".repeat(64)}`);
button.click();
assert.equal(button.disabled, true, "real projection navigation still rejects mismatched rendered receipts");
assert([...e.cards.values()].every(card => !card.open));
"""
    result = subprocess.run(
        [shutil.which("node") or "node", "-e", HARNESS + script],
        input=json.dumps(payload), cwd=ROOT, capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
