from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "demo" / "console"


def test_demo_separates_baseline_formation_from_current_changeset_evidence() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    for selector_id in (
        "active-topology-boundary",
        "active-agent-topology",
        "agent-work-observability",
        "active-native-lifecycle",
    ):
        marker = html.split(f'id="{selector_id}"', 1)[1].split(">", 1)[0]
        assert 'data-evidence-phase="quote-v1-baseline-formation"' in marker

    assert 'id="current-change-evidence"' in html
    assert 'data-evidence-phase="changeset-minimum-team"' in html
    assert 'data-evidence-field="change-team"' in javascript
    assert 'data-evidence-field="exact-owner"' in javascript
    assert 'data-evidence-field="canonical-writes"' in javascript
    assert 'data-evidence-field="agent-work"' in javascript
    assert 'data-capability-semantics="version-ref"' in javascript
    assert 'data-invocation-status="not-observed"' in javascript
    assert 'data-reviewer-task-created="false"' in javascript
    assert '.filter((projection) => projection.hasPreview)' in javascript
    assert "Skill 引用不冒充已装载调用" in javascript
    assert "当前变化轮未新建独立审查任务" in javascript
    assert 'latest.preview_digest !== stored.preview_digest' in shell
    assert "系统可预分配运行身份\uff0c但激活前不能准入任务" in shell
    assert "变化不会建立任务或 run_id" not in shell


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the UI evidence gate")
def test_run_summary_distinguishes_verified_baseline_completion_and_recorded_state() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    helper = "function runSummary" + shell.split("function runSummary", 1)[1].split(
        "\n\n  function taskApprovalCount", 1
    )[0]
    script = helper + r'''
const assert = require('node:assert/strict');
const labels = {runPending:'PENDING',runComplete:'VERIFIED_COMPLETE',runBaseline:'VERIFIED_BASELINE',
 runRecorded:'RECORDED_NOT_VERIFIED',runActive:'ACTIVE'};
function copy(){return labels;}
let proofStatus='BASELINE_VERIFIED';
let currentRunValueProjection=()=>({status:proofStatus});
const state={stage:'CURRENT',business_complete:true,changes:{
 first:{approval:{digest:'approval:first'}},second:{approval:{digest:'approval:second'}}}};
assert.equal(runSummary(state),'VERIFIED_BASELINE','baseline evidence cannot claim completed change verification');
proofStatus='PASS';assert.equal(runSummary(state),'VERIFIED_COMPLETE');
for(const status of ['UNAVAILABLE','WAITING','FAIL']){
 proofStatus=status;
 assert.equal(runSummary(state),'RECORDED_NOT_VERIFIED','completion and every approval alone do not establish verified results');
 assert.equal(runSummary({...state,business_complete:false}),'ACTIVE');
}
currentRunValueProjection=undefined;
assert.equal(runSummary(state),'RECORDED_NOT_VERIFIED','missing verifier cannot upgrade recorded results');
assert.equal(runSummary(null),'PENDING');
currentRunValueProjection=()=>({status:'PASS'});
assert.equal(runSummary({...state,stage:'EMPTY'}),'PENDING','an empty workspace cannot borrow completion');
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", script], cwd=ROOT,
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the UI evidence gate")
def test_current_changeset_projection_is_exactly_bound_and_fail_closed() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    digest_helper = "function isSha256Digest" + javascript.split(
        "function isSha256Digest", 1
    )[1].split("\n\nfunction escapeHtml", 1)[0]
    vmrc_helper = "const VMRC_DISPOSITION_BY_CLASSIFICATION" + javascript.split(
        "const VMRC_DISPOSITION_BY_CLASSIFICATION", 1
    )[1].split("\n\nconst PREVIEW_KIND_OBJECT_IDS", 1)[0]
    receipt_helper = "function receiptForChange" + javascript.split(
        "function receiptForChange", 1
    )[1].split("\n\nfunction countOrObserved", 1)[0]
    count_helper = "function countOrObserved" + javascript.split(
        "function countOrObserved", 1
    )[1].split("\n\nconst CURRENT_CHANGE_TEAM_CONTRACT", 1)[0]
    projection_helper = "const CURRENT_CHANGE_TEAM_CONTRACT" + javascript.split(
        "const CURRENT_CHANGE_TEAM_CONTRACT", 1
    )[1].split("\n\nconst BUSINESS_CHANGE_OBJECT_KEYS", 1)[0]
    node_script = (
        digest_helper
        + "\n\n"
        + vmrc_helper
        + "\n\n"
        + receipt_helper
        + "\n\n"
        + count_helper
        + "\n\n"
        + projection_helper
        + r'''
const assert = require("node:assert/strict");

function buildRound(kind) {
  const runId = "run:current";
  const nonce = "nonce:current";
  const previewDigest = `preview:${kind}`;
  const changeSetDigest = `changeset-digest:${kind}`;
  const planDigest = `plan:${kind}`;
  const coordinationDigest = `coordination:${kind}`;
  const isCurrency = kind === "currency";
  const agents = isCurrency
    ? ["change-coordinator", "finance-steward", "gtm-steward"]
    : ["change-coordinator", "product-steward", "gtm-steward"];
  const owner = isCurrency ? "human:evergreen-finance-owner" : "human:evergreen-product-owner";
  const objectId = isCurrency ? "policy:finance.currency" : "claim:product.launch_date";
  const outputKinds = ["TaskGraph", "SemanticExplanation", "ImpactCandidate"];
  const tasks = agents.map((agent, index) => ({
    id: `task:${kind}:${index}`,
    digest: `task-digest:${kind}:${index}`,
    agent_name: agent,
    candidate_only: true,
    allowed_output_kinds: [outputKinds[index]],
    purpose: `purpose:${agent}`,
    context_scope: [objectId],
  }));
  const runs = tasks.map((task, index) => ({
    agent_name: task.agent_name,
    digest: `run-digest:${kind}:${index}`,
    output_digest: `output:${kind}:${index}`,
    workflow_run_id: runId,
    run_nonce: nonce,
    status: "TRUSTED_COMPLETE",
    evidence_class: "LOCAL_DETERMINISTIC",
    task_id: task.id,
    tool_versions: ["tool:none-proposal-only@v1"],
    skill_versions: ["skill:structured-domain-handoff@1.0"],
  }));
  const handoffs = tasks.map((task, index) => ({
    id: `handoff:${kind}:${index}`,
    digest: `handoff-digest:${kind}:${index}`,
    task_id: task.id,
    delegation_task_digest: task.digest,
    from_agent: task.agent_name,
    to_agent: "orgrebase-control-plane",
    workflow_run_id: runId,
    run_nonce: nonce,
    candidate_only: true,
    orchestration_plan_digest: planDigest,
    payload: {
      candidate_only: true,
      kind: outputKinds[index],
      preview_digest: previewDigest,
      change_object_ids: [objectId],
      target_writes: 0,
    },
  }));
  const decisions = handoffs.map((handoff) => ({
    producer_worker: handoff.from_agent,
    artifact_ref: handoff.id,
    artifact_digest: handoff.digest,
    decision: "ADVISORY_ACCEPTED",
    admitted_effects: [],
  }));
  return {
    runId,
    change: {
      preview: {
        preview_digest: previewDigest,
        bundle: {
          run_envelope: { run_id: runId, nonce },
          change_set: {
            id: `changeset:${kind}`,
            digest: changeSetDigest,
            owner_id: owner,
            deltas: [{ base_value: "before", proposed_value: "after" }],
          },
          change_spec: { id: `changeset:${kind}`, owner_id: owner, object_id: objectId },
          advisory: {
            orchestration_plan: {
              digest: planDigest,
              preview_digest: previewDigest,
              change_set_digest: changeSetDigest,
              tasks,
            },
            agent_runs: runs,
            handoffs,
            compilation_receipt: {
              preview_digest: previewDigest,
              change_set_digest: changeSetDigest,
              orchestration_plan_digest: planDigest,
              identity_digests: agents.map((name) => ({ name, digest: `identity:${name}` })),
            },
            coordination_receipt: {
              digest: coordinationDigest,
              workflow_run_id: runId,
              run_nonce: nonce,
              orchestration_plan_digest: planDigest,
              agent_run_digests: runs.map((run) => run.digest),
              handoff_digests: handoffs.map((handoff) => handoff.digest),
              status: "PASS",
            },
            ingestion_receipt: {
              run_id: runId,
              nonce,
              preview_digest: previewDigest,
              change_set_digest: changeSetDigest,
              orchestration_plan_digest: planDigest,
              live_receipt_digest: coordinationDigest,
              target_writes: 0,
              verifier_evidence_class: "LOCAL_DETERMINISTIC",
              admitted_candidate_digests: handoffs.map((handoff) => handoff.digest),
              rejected_candidate_digests: [],
              decisions,
            },
          },
        },
      },
    },
  };
}

for (const kind of ["launch_date", "currency"]) {
  const fixture = buildRound(kind);
  const valid = changeProjection(kind, fixture.change, fixture.runId);
  assert.equal(valid.changeEvidenceValid, true);
  assert.equal(valid.hasPreview, true);
  assert.equal(valid.candidateTargetWrites, 0);
  assert.equal(valid.agentEvidence.length, 3);
  assert.equal(valid.agentEvidence.every((agent) => agent.bindingValid), true);
  assert.equal(valid.agentEvidence.every((agent) => agent.candidateOnly && agent.targetWrites === 0), true);
  assert.equal(valid.agentEvidence.every((agent) => agent.tools[0] === "tool:none-proposal-only@v1"), true);
  assert.equal(valid.agentEvidence.every((agent) => agent.skills[0] === "skill:structured-domain-handoff@1.0"), true);

  const archived = structuredClone(fixture.change);
  for (const handoff of archived.preview.bundle.advisory.handoffs) {
    handoff.payload.change_object_id = handoff.payload.change_object_ids[0];
    delete handoff.payload.change_object_ids;
  }
  assert.equal(changeProjection(kind, archived, fixture.runId).changeEvidenceValid, true);

  const mutations = {
    wrongRun: (change) => { change.preview.bundle.advisory.agent_runs[0].workflow_run_id = "run:other"; },
    wrongNonce: (change) => { change.preview.bundle.advisory.handoffs[0].run_nonce = "nonce:other"; },
    wrongPreview: (change) => { change.preview.bundle.advisory.handoffs[0].payload.preview_digest = "preview:other"; },
    wrongOwner: (change) => { change.preview.bundle.change_spec.owner_id = "human:other"; },
    missingHandoff: (change) => { change.preview.bundle.advisory.handoffs.pop(); },
    targetWrite: (change) => { change.preview.bundle.advisory.handoffs[0].payload.target_writes = 1; },
    admittedEffect: (change) => { change.preview.bundle.advisory.ingestion_receipt.decisions[0].admitted_effects.push("effect:1"); },
    missingAgentRuns: (change) => { change.preview.bundle.advisory.agent_runs = []; },
    missingChangeObject: (change) => { change.preview.bundle.advisory.handoffs[0].payload.change_object_ids = []; },
    extraChangeObject: (change) => { change.preview.bundle.advisory.handoffs[0].payload.change_object_ids.push("source:other"); },
    malformedChangeObjects: (change) => {
      const payload = change.preview.bundle.advisory.handoffs[0].payload;
      payload.change_object_id = payload.change_object_ids[0];
      payload.change_object_ids = null;
    },
  };
  for (const mutate of Object.values(mutations)) {
    const changed = structuredClone(fixture.change);
    mutate(changed);
    const invalid = changeProjection(kind, changed, fixture.runId);
    assert.equal(invalid.hasPreview, true);
    assert.equal(invalid.changeEvidenceValid, false);
    assert.equal(invalid.candidateTargetWrites, null);
  }
}
'''
    )
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
