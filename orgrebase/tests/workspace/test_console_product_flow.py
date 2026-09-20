from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from orgrebase.api import _workspace_current_run_archive_view
from orgrebase.workspace.completed_run_observability import (
    build_completed_run_observability_from_trusted_state,
)

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "demo" / "console"


def test_console_is_one_recoverable_workspace_product_flow() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert "企业工作持续演化引擎" in html
    assert 'id="scenario-eyebrow"' in html
    assert 'id="profile-data-badge"' in html
    assert 'class="badge badge-muted badge-boundary"' in html
    assert 'data-i18n="header.realEnterpriseNotRun" hidden aria-hidden="true"' in html
    styles = (CONSOLE / "styles.css").read_text(encoding="utf-8")
    assert ".runtime-badges #profile-data-badge { display: none; }" in styles
    assert 'data-i18n="header.currentTask.waiting"' in html
    assert "RUN CHECK: CHECKING" in javascript
    assert "ORCHESTRATION: AGENTTEAMS" in javascript
    assert "PRODUCTION READY: NO · ENTERPRISE VALIDATION PENDING" in javascript
    assert "frozen live-run candidate receipt" in javascript
    assert "SEPARATE RUN · SIGKILL RECOVERY" in javascript
    assert "DISTRIBUTED RECOVERY REQUIRES PRODUCTION VALIDATION" in javascript
    assert "INDEPENDENT RETAINED MECHANISM EVIDENCE" not in html
    assert "独立机制验证档案" in html
    assert "HISTORICAL RELIABILITY VERIFICATION" in javascript
    assert "企业规则变化后，OrgRebase识别受影响的工作" in html
    assert "当前支持企业报价流程。" in html
    assert "AgentTeams 候选结果" in html
    assert "oac_admission" in javascript
    assert "NOT_USED_IN_THIS_RUN" in javascript
    assert "RUN CHECK: PASS" in javascript
    assert 'id="proof-at-actions"' not in html
    assert 'id="proof-final-quote"' not in html
    assert "/api/platform/evidence" in javascript
    assert "/api/semifinal/evidence" not in javascript
    assert 'id="dependency-tool-status"' in html
    assert 'id="dependency-tool-receipt"' in html
    assert 'localizedFactHtml("SUCCEEDED")' in javascript
    assert 'localizedFactHtml("READ_ONLY")' in javascript
    assert "dependency_evidence_tool" in javascript
    assert 'skill.authorization_mode === "RELEASE"' in javascript
    assert 'skill.release_state === "CANARY"' in javascript
    assert "RELEASE · CANARY · 8/8" in javascript
    assert "skill.release_receipt_digest" in javascript
    assert "SAME_RUN_CONTROLLED_LOCAL_RELEASE_AUTHORITY" in javascript

    required_endpoints = {
        "/api/workspace/state",
        "/api/workspace/export/quote",
        "/api/workspace/export/evidence",
        "/api/workspace/operating-model",
        "/api/workspace/run-archive",
        "/api/workspace/run-observability",
        "/api/public-real-process/validation",
    }
    assert all(endpoint in javascript for endpoint in required_endpoints)
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    for endpoint in (
        "/api/workspace/task-intake/prepare",
        "/api/workspace/task-intake/admit",
        "/api/workspace/task-intake/run",
    ):
        assert endpoint in shell
    assert "/api/workspace/form" not in javascript
    assert "/api/demo/" not in javascript
    assert "workspace-button" not in html
    assert "preview-button" not in html
    assert "apply-button" not in html
    assert "reset-action" not in html
    assert "/api/workspace/reset" not in javascript
    assert "原流程难点与 OrgRebase 解法" in html
    assert "function renderTimeline(state)" in javascript
    assert "state.change_events" in javascript
    assert "HUMAN_REVIEW_HOLD_MS = 4000" in javascript
    assert "reviewGateReadyAt" in javascript
    assert "REVIEW_GATE_NOT_READY" in (CONSOLE / "change-workbench.js").read_text()
    assert "服务端四秒审阅门尚未到时" in javascript
    assert "人工确认批准" in javascript
    assert 'action.method === "approve" && remaining > 0' in javascript
    assert 'id="cockpit-scene"' in html
    assert 'id="cockpit-value"' in html
    assert 'id="cockpit-collaboration"' in html
    assert 'id="cockpit-operations"' in html
    assert 'id="active-run-id"' in html
    assert 'id="active-action-ledger"' in html
    assert 'id="retained-action-ledger"' in html
    assert "另一条冻结运行" in html
    assert "DETERMINISTIC_DOMAIN_PROVIDERS" in javascript
    assert "agent_runs" in javascript
    assert "handoffs" in javascript
    assert "evidence_spine" in javascript
    assert "agent_collaboration" in javascript
    assert "value_and_responsibility" in javascript
    assert "renderValueAndResponsibility" in javascript
    retained_renderer = javascript.split("function renderRetainedEvidence", 1)[1].split(
        "\nfunction operationReceiptCard", 1
    )[0]
    assert "renderValueAndResponsibility" not in retained_renderer
    assert "currentOperatingModel || { status: \"UNAVAILABLE\" }" in javascript
    assert '"/api/workspace/operating-model"' in javascript
    assert 'async function refreshSupportingViews()' in javascript
    assert "REVIEWER PASS · CANONICAL PERSISTED" in javascript
    assert "MODELLED_COUNTERFACTUAL" in javascript
    assert "历史成本模型不作为本次结果" in html
    assert "未换算为金额、工时或企业 ROI" in javascript
    assert 'id="value-process-steps"' in html
    assert 'title="${escapeHtml(step.label)}"' not in javascript
    assert 'title="${escapeHtml(step.as_is_interface_class)}"' not in javascript
    assert 'id="value-metrics"' in html
    assert 'id="value-cost-model"' in html
    assert 'id="public-real-process-validation"' in html
    assert 'id="public-validation-status"' in html
    assert 'id="public-scope-vendor"' in html
    assert 'id="public-scope-document"' in html
    assert 'id="public-scope-oac"' in html
    assert 'id="public-oac-adaptation"' in html
    assert 'id="public-adaptation-mapper"' in html
    assert 'id="public-adaptation-validator"' in html
    assert 'id="public-adaptation-unknowns"' in html
    assert 'id="public-adaptation-gate"' in html
    assert 'id="public-adaptation-queries"' in html
    assert 'id="public-adaptation-recall"' in html
    assert 'id="public-adaptation-unsafe"' in html
    for proof_id in (
        "current-proof-overview",
        "proof-chain-golden",
        "proof-chain-oac",
        "proof-chain-bpi",
        "proof-chain-formation",
        "proof-chain-agentic",
        "proof-product-path",
        "product-path-detail",
    ):
        assert f'id="{proof_id}"' in html
    assert '"/api/release-facts"' in javascript
    assert 'release.schema_version === "orgrebase.release-facts.v9"' in javascript
    assert 'productPath.benchmark_version === "ProductPath-v0.3-task-intake-bound"' in javascript
    assert 'productPath.evidence_class === "LOCAL_REAL_HTTP_BLACKBOX"' in javascript
    assert "completedCountPair(productPath.case_count, productPath.cases_passed" in javascript
    assert "completedCountPair(productPath.mutation_count, productPath.mutations_killed," in javascript
    assert "productPath.case_count === 12" not in javascript
    assert "formation.domain_task_count === 2" not in javascript
    assert "formation.reviewer_task_count === 1" not in javascript
    assert 'id="oac-enhanced-assurance" hidden aria-hidden="true"' in html
    assert 'id="public-adaptation-writes"' in html
    assert "renderPublicRealProcessValidation" in javascript
    assert "66549" not in html
    assert "0.948684" not in html
    assert "0.99698" not in html
    assert "validation.action_scopes" in javascript
    assert "validation.assurance" in javascript
    assert "validation.agentic_adaptation" in javascript
    assert 'agentic.status === "PASS"' in javascript
    assert "adaptationAgentTeams.action_count" in javascript
    assert "adaptationMapping.unknown_count" in javascript
    assert "adaptationAdmission.elapsed_ms" in javascript
    assert "adaptationExecution.canonical_target_writes" in javascript
    assert 'source.official_record.startsWith("https://")' in javascript
    assert "已验证类型化范围、保守停手和来源血缘" in html
    assert "非人工因果标注" in html
    assert "CURRENT_RUN_FREEZE_PENDING" in javascript
    assert 'operationReceiptCard(\n      t("operations.card.frozen")' not in javascript
    for marker in (
        'id="completion-total"',
        'id="completion-checklist"',
        'class="semifinal-rubric"',
        'class="judge-checklist"',
        "goldenJudgeGates",
        "official_completion",
        "completionItems",
        "rubric.",
        "gate.p0",
        "gate.p1",
        "ACTIVE-RUN SEMIFINAL EVIDENCE GATES",
        "当前运行的复赛证据门禁",
    ):
        assert marker not in html
        assert marker not in javascript
    for production_artifact in ("评委", "复赛", "竞赛", "评分", "权重", "Spec 053", "Spec 060", "P0-", "P1-"):
        assert production_artifact not in html
        assert production_artifact not in javascript
    visible_html = re.sub(r"<[^>]+>", " ", html)
    for weight in ("20%", "25%", "30%", "5%"):
        assert weight not in visible_html
    for internal_workbench_copy in ("机制证据", "Golden 运行", "证据中枢", "证据脊柱"):
        assert internal_workbench_copy not in visible_html
    assert 'id="retained-lifecycle"' in html
    assert 'id="retained-authority"' in html
    assert "collaboration.formation_taskflow" in javascript
    assert 'id="retained-mechanism"' in html
    assert "validationView.insertBefore(publicValidation, currentProof)" in shell
    assert "validationView.append(retainedMechanism)" in shell
    assert "formation.planned_domain_ids" in javascript
    assert "formation.actual_agentteams_domain_ids" in javascript
    assert "formation.topology_match === true" in javascript
    assert "reviewerDependencies.every" in javascript
    assert "formation.canonical_target_writes === 0" in javascript
    assert "动态组队计划对账" in javascript
    assert "动态组队计划 {planned} 个领域 = AgentTeams 实际 {actual} 个领域" in javascript
    assert "未展示计划或实际拓扑" in javascript
    assert 'name: t("formationProof.reviewerName")' in javascript
    assert "exactIdentifier: reviewer.assignee_actor_id" in javascript
    assert 'name: t("topology.name.quoteTaskCoordinator")' in javascript
    assert "exactIdentifier: collaboration.project_id" in javascript
    assert 'nodeField(t("topology.detail.upstream")' in javascript
    assert 'nodeField(t("topology.detail.authority")' in javascript
    assert 'nodeField(t("topology.auditIdentifier"), exactIdentifier)' not in javascript
    assert "业务、协作与运维共用同一运行链路" in html
    assert "市场与商业化智能体 ↔ 市场与商业化负责人" in shell
    assert "协作引擎：AgentTeams" in html
    assert "智能体使用受治理 Skill 组装候选" in shell
    assert "组织智能体契约（OAC）企业适配层" in html
    assert "现状 R 是当前人工执行者" in html
    assert "变化后执行者是智能体或受控系统" in html
    assert "A 仍是最终负责与批准的人" in html
    assert "开发者接口（OpenAPI）" in html
    assert 'state.stage === "EMPTY"' in javascript
    assert 'state.business_complete === true' in javascript
    assert '"collaboration.local.active.title"' in javascript
    assert '"collaboration.local.complete.title"' in javascript
    assert "本次成果与对应完成回执已核验，变更、批准和重构记录按实际运行展示" in javascript
    assert "唯一运行编号（run_id）" not in html
    for product_status in (
        "impact.previewEvidenceBound",
        "impact.certificateVerified",
        "governance.approvalsBound",
        "governance.successorReady",
        "governance.toolReceiptBound",
    ):
        assert product_status in javascript
    for audit_target in (
        'exactAuditTitle("preview-digest")',
        'exactAuditTitle("certificate-digest")',
        'exactAuditTitle("approval-digest")',
        'exactAuditTitle("successor-graph")',
        'exactAuditTitle("dependency-tool-receipt")',
        'exactAuditTitle("event-chain-detail")',
    ):
        assert audit_target in javascript
    for compensation_projection in (
        "canonical_transaction_atomic_rollback",
        "downstream_state_compensation",
        "reversible_external_git_compensation",
        "real_enterprise_connector_compensation",
        "compensation_evidence",
    ):
        assert compensation_projection in javascript
    assert 't("retained.skill.invocation"' in javascript
    assert 'id="ops-integrations"' in html
    assert 'id="ops-otlp-fields"' in html
    assert 'byId("ops-otlp-fields").title = otlpFields.join(" · ")' not in javascript
    assert 't(OTLP_AUDIT_FIELD_LABELS[field] || field)' in javascript
    assert 'id="ops-enterprise-readiness"' in html
    assert 'id="ops-audit-details"' in html
    assert 'id="ops-audit-details" open' not in html
    assert 'id="ops-audit-content"' in html
    assert "renderOperationsAudit(evidence, operations, proofVerified)" in javascript
    operations_renderer = javascript.split("function renderOperations(state)", 1)[1].split(
        "\nfunction renderEvidenceCockpit", 1
    )[0]
    assert 'currentHealth === null ? "CHECKING"' in operations_renderer
    assert 'currentReadiness === null ? "CHECKING"' in operations_renderer
    assert 'const workspaceLoading = currentState === null && workspaceStateAvailability === "loading"' in operations_renderer
    assert 'workspaceLoading ? "CHECKING"' in operations_renderer
    assert "if (currentRetainedEvidence === null)" in operations_renderer
    assert 't("proofOverview.loading")' in operations_renderer
    assert operations_renderer.index("currentRetainedEvidence === null") < operations_renderer.index(
        "renderEvidenceIndexArchive(evidence)"
    )
    assert "evidence.run_id" in javascript
    assert "otlp.signals" in javascript
    assert "otlp.key_fields" in javascript
    for connector in ("crm", "cpq", "clm", "erp", "knowledge_base"):
        assert f'{connector}: "operations.audit.connector.' in javascript
    assert "retention.indexed_telemetry_seconds" in javascript
    assert "retention.rejected_payload_diagnostic_seconds" in javascript
    assert "retention.canonical_business_evidence_affected === false" in javascript
    assert "readiness.production_ready" in javascript
    assert "readiness.contractual_sla" in javascript
    assert "readiness.geographic_failover" in javascript
    assert "2592000" not in html + javascript
    assert "604800" not in html + javascript
    assert "sbom_component_count" in javascript
    assert "artifact_version" in javascript
    assert "operations.sameRun.candidateLane" in javascript
    assert "operations.sameRun.observabilityLane" in javascript
    assert "operations.sameRun.governanceLane" in javascript
    assert "negative_probe" in javascript
    assert "故障探针捕获" in javascript
    assert "健康链 0 个" not in javascript
    assert "健康链已通过（当前告警 {healthy} 个）" in javascript
    assert "displayToken(negativeAlert.severity" in javascript
    assert "displayToken(item.stage)" in javascript
    assert "7 层运行记录" not in javascript
    assert "Seven runtime layers" not in javascript
    assert 'api("/api/health",' in javascript
    assert 'api("/readyz",' in javascript


def test_runtime_assurance_exposes_a_fail_closed_frozen_evidence_index_and_vmrc() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    styles = (CONSOLE / "styles.css").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    evidence_marker = (
        '<details class="ops-audit-details evidence-index-archive" '
        'id="evidence-index-archive" data-evidence-lane="frozen-independent-validation">'
    )
    assert evidence_marker in html
    assert evidence_marker.replace(">", " open>") not in html
    assert html.index('id="cockpit-operations"') < html.index(evidence_marker)
    assert html.index(evidence_marker) < html.index('id="workspace"')
    for element_id in (
        "evidence-index-status",
        "evidence-index-run-id",
        "evidence-index-entry-count",
        "evidence-index-digest",
        "evidence-index-pack-digest",
        "evidence-index-classes",
        "evidence-index-verifier",
        "evidence-index-privacy",
        "evidence-index-target-writes",
        "evidence-index-boundary",
    ):
        assert f'id="{element_id}"' in html

    assert "function exactFrozenEvidenceIndex(evidence)" in javascript
    assert "renderEvidenceIndexArchive(evidence);" in javascript
    assert 'index.run_id !== evidence.run_id' in javascript
    assert 'privacy.sensitive_values_disclosed !== false' in javascript
    assert "非本次报价运行 · 不借证" in html
    assert "NOT THE ACTIVE QUOTE RUN · NO BORROWED PROOF" in javascript
    assert ".evidence-index-archive > summary > em" in styles

    assert "审批前预演 + 最小重构证书（VMRC）" in html
    assert "Pre-approval Preview + VMRC Minimal Rebuild Certificate" in javascript
    assert 'id="vmrc-boundary"' in html
    assert "function exactVmrcBinding(bundle)" in javascript
    assert "VMRC_DISPOSITION_BY_CLASSIFICATION" in javascript
    assert 'certificate.preview_digest !== preview.digest' in javascript
    assert 'certificate.change_set_digest !== changeSet.digest' in javascript
    assert 'effect.impact_result_digest !== result.digest' in javascript
    assert 'certificateNode.dataset.state = "invalid"' in javascript
    assert html.index('id="vmrc-boundary"') < html.index('id="approval-actor"')
    assert "当前运维状态 + 独立保障档案" in shell
    assert "Current operational state + independent assurance archives" in shell


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the Evidence Index gate")
def test_frozen_evidence_index_projection_rejects_partial_or_cross_run_evidence() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    digest_helper = "function isSha256Digest" + javascript.split(
        "function isSha256Digest", 1
    )[1].split("\n\nfunction escapeHtml", 1)[0]
    index_helper = "function exactFrozenEvidenceIndex" + javascript.split(
        "function exactFrozenEvidenceIndex", 1
    )[1].split("\n\nfunction renderEvidenceIndexArchive", 1)[0]
    node_script = digest_helper + "\n" + index_helper + r"""
const assert = require("node:assert/strict");
const digest = (digit) => `sha256:${digit.repeat(64)}`;
const valid = {
  status: "PASS",
  verification_status: "PASS",
  archive_status: "FROZEN_HISTORICAL_VALIDATION",
  run_id: "run:frozen",
  current_task_run: false,
  current_pack_pilot_run: false,
  pack_pilot_binding: "NOT_SAME_RUN",
  evidence_index: {
    schema_version: "orgrebase.semifinal-closure-evidence-index.v1",
    status: "PASS",
    archive_status: "FROZEN_HISTORICAL_VALIDATION",
    run_id: "run:frozen",
    current_task_run: false,
    entry_count: 148,
    index_digest: digest("a"),
    pack_digest: digest("b"),
    evidence_classes: ["CONTROLLED_LOCAL_REAL_HTTP", "CONTROLLED_LOCAL_NATIVE_TASKFLOW"],
    verifier_status: "PASS",
    privacy: {
      canary_status: "PASS",
      canary_reason_code: "PRIVACY_PAYLOAD_REJECTED",
      rejection_reason_code: "OTLP_RESTRICTED_FIELD:secret",
      raw_payload_retained: false,
      sensitive_values_disclosed: false,
    },
    target_writes: 0,
  },
};
assert.equal(exactFrozenEvidenceIndex(valid), valid.evidence_index);
const invalid = {};
for (const [name, mutate] of Object.entries({
  crossRun: (value) => { value.evidence_index.run_id = "run:other"; },
  currentRun: (value) => { value.current_task_run = true; },
  missingEntries: (value) => { value.evidence_index.entry_count = 0; },
  invalidDigest: (value) => { value.evidence_index.pack_digest = "sha256:not-a-digest"; },
  failedVerifier: (value) => { value.evidence_index.verifier_status = "FAIL"; },
  retainedRaw: (value) => { value.evidence_index.privacy.raw_payload_retained = true; },
  disclosedSensitive: (value) => { value.evidence_index.privacy.sensitive_values_disclosed = true; },
  targetWrite: (value) => { value.evidence_index.target_writes = 1; },
})) {
  const value = structuredClone(valid);
  mutate(value);
  invalid[name] = exactFrozenEvidenceIndex(value);
}
assert.deepEqual(invalid, {
  crossRun: null,
  currentRun: null,
  missingEntries: null,
  invalidDigest: null,
  failedVerifier: null,
  retainedRaw: null,
  disclosedSensitive: null,
  targetWrite: null,
});
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the VMRC binding gate")
def test_vmrc_exact_binding_rejects_mismatch_missing_and_nonminimal_effects() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    digest_helper = "function isSha256Digest" + javascript.split(
        "function isSha256Digest", 1
    )[1].split("\n\nfunction escapeHtml", 1)[0]
    vmrc_helper = "const VMRC_DISPOSITION_BY_CLASSIFICATION" + javascript.split(
        "const VMRC_DISPOSITION_BY_CLASSIFICATION", 1
    )[1].split("\n\nconst PREVIEW_KIND_OBJECT_IDS", 1)[0]
    node_script = digest_helper + "\n" + vmrc_helper + r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const digest = (digit) => `sha256:${digit.repeat(64)}`;
const valid = {
  change_set: { digest: digest("a") },
  preview: {
    digest: digest("b"),
    revision_lock: { digest: digest("c") },
    results: [{
      object_id: "work:quote-a",
      classification: "AFFECTED_HARD",
      digest: digest("d"),
    }],
  },
  minimal_rebase_certificate: {
    schema_version: "orgrebase.minimal-rebase-certificate.v1",
    verifier_version: "orgrebase.minimal-rebase-verifier@1.0.0",
    digest: digest("e"),
    impact_certificate_set_digest: digest("f"),
    change_set_digest: digest("a"),
    preview_digest: digest("b"),
    revision_lock_digest: digest("c"),
    effects: [{
      target_id: "work:quote-a",
      disposition: "REBUILD",
      impact_result_digest: digest("d"),
      impact_certificate_digest: digest("0"),
    }],
  },
};
assert.equal(exactVmrcBinding(valid), valid.minimal_rebase_certificate);
const goldenState = JSON.parse(fs.readFileSync("evidence/golden-competition/latest/pilot/state.json", "utf8"));
assert.ok(exactVmrcBinding(goldenState.latest_preview.bundle));
const unknownHold = structuredClone(valid);
unknownHold.preview.results[0].classification = "UNKNOWN";
unknownHold.minimal_rebase_certificate.effects[0].disposition = "HOLD_FOR_REVIEW";
assert.equal(exactVmrcBinding(unknownHold), unknownHold.minimal_rebase_certificate);

const checks = {};
for (const [name, mutate] of Object.entries({
  wrongChangeSet: (value) => { value.minimal_rebase_certificate.change_set_digest = digest("1"); },
  wrongPreview: (value) => { value.minimal_rebase_certificate.preview_digest = digest("1"); },
  wrongRevisionLock: (value) => { value.minimal_rebase_certificate.revision_lock_digest = digest("1"); },
  wrongResult: (value) => { value.minimal_rebase_certificate.effects[0].impact_result_digest = digest("1"); },
  missingEffect: (value) => { value.minimal_rebase_certificate.effects = []; },
  extraEffect: (value) => { value.minimal_rebase_certificate.effects.push({ ...value.minimal_rebase_certificate.effects[0], target_id: "work:extra" }); },
  extraRebuild: (value) => { value.preview.results[0].classification = "UNKNOWN"; },
  unknownPreserved: (value) => { value.preview.results[0].classification = "UNKNOWN"; value.minimal_rebase_certificate.effects[0].disposition = "PRESERVE_WITHIN_BOUNDARY"; },
  wrongSchema: (value) => { value.minimal_rebase_certificate.schema_version = "v0"; },
  missingCertificate: (value) => { delete value.minimal_rebase_certificate; },
})) {
  const value = structuredClone(valid);
  mutate(value);
  checks[name] = exactVmrcBinding(value);
}
for (const value of Object.values(checks)) assert.equal(value, null);
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_agent_work_observability_is_same_run_progressive_and_receipt_driven() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    styles = (CONSOLE / "styles.css").read_text(encoding="utf-8")
    renderer = javascript.split("const AGENT_WORK_PROFILES", 1)[1].split(
        "function renderGoldenCollaboration", 1
    )[0]

    assert 'id="agent-work-observability"' in html
    assert 'id="agent-work-expand"' in html
    assert 'id="agent-work-collapse"' in html
    assert 'data-route-target="data"' in html
    for actor in (
        "change-coordinator",
        "product-steward",
        "legal-steward",
        "finance-steward",
        "gtm-steward",
        "independent-reviewer",
    ):
        assert f'actor: "{actor}"' in renderer

    assert "sameRunActivityRow(row, runId)" in renderer
    assert "AGENT_WORK_ACTIVITY_PLANES.has(row.plane)" in renderer
    assert "row.tool_or_skill" in renderer
    assert "isObservedLoadedSkill" in renderer
    assert 'entry.plane === "SKILL" && value.startsWith("skill-package:")' in renderer
    assert 't("agentWork.noLoadedSkill")' in renderer
    assert "run.skill_versions" not in renderer
    assert "profile.skill" not in renderer
    assert 'data-agent-actor="${escapeHtml(profile.actor)}"' in renderer
    assert 'data-plane="${escapeHtml(row.plane)}"' in renderer
    assert "state.agent_candidate_outputs" in renderer
    assert 'data-candidate-view-status="PASS"' in renderer
    assert 'data-task-id="${escapeHtml(output.task_id' in renderer
    assert 'data-candidate-predicate="${escapeHtml(candidate.predicate' in renderer
    assert "candidate.source_ref" in renderer
    assert "candidate.transformation_ref" in renderer
    assert 'data-review-verdict="${escapeHtml(decision.verdict' in renderer
    assert 'kind: "business-recovery-tool"' in renderer
    assert 'kind: "post-formation-audit-tool"' in renderer
    assert 'kind: "loaded-skill-package"' in renderer
    assert 'kind: "advisory-skill-ref"' in renderer
    assert "工作区服务读取已形成的只读依赖图" in javascript
    assert "市场与商业化执行者仅是回执主体" in javascript
    golden_state = json.loads(
        (ROOT / "evidence/golden-competition/latest/pilot/state.json").read_text(
            encoding="utf-8"
        )
    )
    observed_capabilities = {
        row["tool_or_skill"]
        for row in golden_state["execution_activity"]["rows"]
        if row.get("tool_or_skill")
    }
    assert "skill-package:enterprise-quote-compose@1.3.1" in observed_capabilities
    tool_rows = {
        row["action"]: row
        for row in golden_state["execution_activity"]["rows"]
        if row["plane"] == "TOOL"
    }
    assert tool_rows["RECOVER_FINANCE_DEPENDENCY"]["actor_or_domain"] == "finance-steward"
    assert tool_rows["RECOVER_FINANCE_DEPENDENCY"]["tool_or_skill"] == "READ_DEPENDENCY_EVIDENCE"
    assert tool_rows["READ_DEPENDENCY_EVIDENCE"]["actor_or_domain"] == "gtm-steward"
    assert tool_rows["READ_DEPENDENCY_EVIDENCE"]["tool_or_skill"] == "tool:dependency-evidence@1.0.0"
    assert 'document.querySelectorAll("#agent-work-list > details")' in javascript
    assert ".agent-work-card[open] { grid-column: 1 / -1;" in styles
    assert '.agent-work-capabilities .skill-boundary' in styles


def test_business_data_evolution_renders_values_context_and_selective_object_receipts() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    styles = (CONSOLE / "styles.css").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    source_renderer = javascript.split("function renderEnterpriseDataSource", 1)[1].split(
        "function renderEnterpriseDataOac", 1
    )[0]
    context_renderer = javascript.split("function renderEnterpriseDataContext", 1)[1].split(
        "function renderEnterpriseDataQuotes", 1
    )[0]
    projection = javascript.split("function changeProjection", 1)[1].split(
        "function quoteVersionFromRef", 1
    )[0]
    round_renderer = javascript.split("function businessChangeRound", 1)[1].split(
        "function summedProjectionMetric", 1
    )[0]

    assert "source.values" in source_renderer
    assert "value.source_ref" in source_renderer
    assert "value.authority_ref" in source_renderer
    assert "value.value" in source_renderer
    assert "value.semantic_kind" in source_renderer
    assert 't("dataJourney.source.capabilityRequirement")' in source_renderer
    assert 'class="enterprise-data-source-values"' in source_renderer
    assert 'data-domain="${escapeHtml(value.domain_id' in source_renderer
    assert "actor.included_slot_ids" in context_renderer
    assert 'class="enterprise-data-context-actors"' in context_renderer
    assert "actor.projection_digest" in context_renderer
    assert "receipt.applied_claims" in projection
    assert "receipt.bounded_unaffected" in projection
    assert "receipt.unknown" in projection
    for object_class in ("applied", "preserved", "review"):
        assert f'businessChangeObjectGroup("{object_class}"' in round_renderer
        assert 'data-object-class="${escapeHtml(objectClass)}"' in javascript
    assert ".business-change-objects" in styles
    assert 'changes: "业务数据演化与验收"' in shell
    assert 'changes: "Data Evolution & Acceptance"' in shell
    assert 'id="selective-change-card"' in html
    assert 'data-replaced-by="business-change-board" hidden' in html
    duplicate_renderer = javascript.split("function renderSelectiveChangeCard", 1)[1].split(
        "const PROCESS_LABEL_KEYS", 1
    )[0]
    assert "card.hidden = true" in duplicate_renderer
    assert 'byId("selective-change-rounds").innerHTML = ""' in duplicate_renderer


def test_quote_compose_requirement_and_loaded_package_are_not_presented_as_a_version_conflict() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    state = json.loads(
        (ROOT / "evidence/golden-competition/latest/pilot/state.json").read_text(
            encoding="utf-8"
        )
    )

    assert '"dataJourney.source.capabilityRequirement": "企业能力要求引用 · 不是本次装载的 Skill 包"' in javascript
    assert '"agentWork.capability.loadedSkill": "实际装载 / 评测 / 调用的 Skill 包"' in javascript
    assert 'isCapabilityRequirement ? `<small class="capability-requirement-ref">' in javascript
    assert 'plane === "SKILL" && normalized.startsWith("skill-package:")' in javascript
    requirement = next(
        value
        for value in state["enterprise_data_lineage"]["source"]["values"]
        if value["slot_id"] == "quote_compose_skill"
    )
    loaded = next(
        row
        for row in state["execution_activity"]["rows"]
        if row["plane"] == "SKILL" and row["action"] == "COMPOSE_REVIEWED_QUOTE_CANDIDATE"
    )
    assert requirement["value"] == "skill:enterprise-quote-compose@1.0"
    assert requirement["semantic_kind"] == "SKILL"
    assert loaded["tool_or_skill"] == "skill-package:enterprise-quote-compose@1.3.1"
    assert loaded["target_writes"] == 0


def test_terminal_topology_preserves_each_human_approval_instead_of_only_the_latest() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    topology = javascript.split("const approvalHistory = approvalRecords(state);", 1)[1].split(
        "const storeNode =", 1
    )[0]
    topology_stages = javascript.split(
        'renderDirectedTopology("active-agent-topology", [', 1
    )[1].split("];", 1)[0]

    assert 'state.business_complete === true' in topology
    assert "approvalHistory.length > 0" in topology
    assert "approvalHistory.map((record, index)" in topology
    assert "approvalActor(record)" in topology
    assert "record.approval_review_evidence" in topology
    assert 't("topology.status.humanApprovalBound"' in topology
    assert "round: index + 1" in topology
    assert 'nodes: humanNodes' in topology_stages


def test_mid_width_console_layout_keeps_eight_steps_and_data_stages_readable() -> None:
    styles = (CONSOLE / "styles.css").read_text(encoding="utf-8")
    shell_styles = (CONSOLE / "workspace-shell.css").read_text(encoding="utf-8")
    mid_width = styles.split("@media (min-width: 721px) and (max-width: 1440px)", 1)[1].split(
        "@media (max-width: 1440px)", 1
    )[0]
    mobile = styles.split("@media (max-width: 720px)", 1)[1]

    assert ".timeline { grid-template-columns: repeat(4, minmax(0, 1fr)); }" in mid_width
    assert ".timeline li:nth-child(-n+4) { border-bottom: 1px solid var(--hair); }" in mid_width
    assert ".enterprise-data-journey-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in mid_width
    assert ".enterprise-data-journey-rail > i { display: none; }" in mid_width
    assert ".timeline { grid-template-columns: repeat(2, 1fr); }" in mobile
    assert ".shell-page-header h1:focus { outline: none; }" in shell_styles


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the OAC data-view contract")
def test_enterprise_data_oac_requires_same_run_execution_lineage_before_complete() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    renderer = "function renderEnterpriseDataOac" + javascript.split(
        "function renderEnterpriseDataOac", 1
    )[1].split("function renderEnterpriseDataContext", 1)[0]
    node_script = f"""
const DATA_JOURNEY_COMPONENT_KEYS = {{
  DOMAIN: "domain", KNOWLEDGE: "knowledge", AUTHORITY: "authority",
  CAPABILITY: "capability", DEPENDENCY: "dependency",
}};
const escapeHtml = (value) => String(value);
const dataJourneyVerdict = (value) => String(value || "NOT_OBSERVED");
const dataJourneyComponentLabel = (value) => String(value || "NOT_OBSERVED");
const t = (key, values = {{}}) => JSON.stringify({{ key, values }});
let captured = null;
function setEnterpriseDataJourneyStep(id, state, summary, body) {{
  captured = {{ id, state, summary: JSON.parse(summary), body }};
}}
{renderer}
const bindings = Object.keys(DATA_JOURNEY_COMPONENT_KEYS).map((kind) => ({{
  kind,
  source_admission_verdict: "ADMITTED",
  runtime_projection_status: "MATCH",
}}));
const legacy = {{
  source_admission_verdict: "ADMITTED",
  runtime_projection_verdict: "MATCH",
  activation_status: "CONSUMED_BY_QUOTE_FORMATION",
  component_bindings: bindings,
}};
renderEnterpriseDataOac(null);
const missing = captured;
renderEnterpriseDataOac(legacy);
const oldWithoutLineage = captured;
renderEnterpriseDataOac({{
  ...legacy,
  execution_binding_status: "OAC_BOUND_EXECUTION_PLAN_REALIZED",
  topology_match: true,
  selected_domain_ids: ["product", "legal", "finance", "gtm"],
}});
const bound = captured;
console.log(JSON.stringify({{ missing, oldWithoutLineage, bound }}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)

    assert result["missing"]["state"] == "waiting"
    assert result["oldWithoutLineage"]["state"] == "active"
    assert result["oldWithoutLineage"]["summary"]["key"] == "dataJourney.oac.summaryActivated"
    assert "dataJourney.oac.boundaryPending" in result["oldWithoutLineage"]["body"]
    assert result["bound"]["state"] == "complete"
    assert result["bound"]["summary"] == {
        "key": "dataJourney.oac.summaryBound",
        "values": {"count": 5, "domains": 4},
    }
    assert "dataJourney.oac.boundary" in result["bound"]["body"]
    assert "dataJourney.oac.boundaryPending" not in result["bound"]["body"]


def test_oac_formation_stage_is_fail_closed_and_product_topology_stays_business_readable() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    collaboration = javascript.split("function renderGoldenCollaboration", 1)[1].split(
        "function renderActiveCollaboration", 1
    )[0]
    data_oac = javascript.split("function renderEnterpriseDataOac", 1)[1].split(
        "function renderEnterpriseDataContext", 1
    )[0]
    topology_node = javascript.split("function topologyNode", 1)[1].split(
        "function topologyStage", 1
    )[0]
    topology_summary = topology_node.split("<summary>", 1)[1].split("</summary>", 1)[0]

    for condition in (
        'oacLineage.status === "OAC_BOUND_EXECUTION_PLAN_REALIZED"',
        "oacLineage.topology_match === true",
        "Array.isArray(oacLineage.planned_domain_ids)",
        "oacLineage.planned_domain_ids.length > 0",
    ):
        assert condition in collaboration
    assert "const oacFormationNode = oacExecutionBound ?" in collaboration
    assert 'label: t("topology.stage.oac")' in collaboration
    assert "...(oacFormationNode" in collaboration
    assert 't(oacExecutionBound ? "topology.boundary.activeOac" : "topology.boundary.active"' in collaboration

    # Business summaries and expanded nodes stay readable. Exact lineage roots
    # remain in the downloadable evidence package, not in the operator surface.
    for raw_field in (
        "task_formation_decision_receipt_digest",
        "task_agent_context_envelope_digest",
        "agentteams_execution_plan_digest",
        "organization_snapshot_digest",
        "organizational_demand_digest",
    ):
        assert raw_field not in data_oac
        assert raw_field not in topology_summary
    assert '<details class="topology-node' in topology_node
    assert '<details class="topology-node ${escapeHtml(tone)}" open' not in topology_node
    for technical_field in (
        'nodeField("digest"',
        'nodeField("input digest"',
        'nodeField("output digest"',
        'nodeField("provider request"',
        'nodeField("trace"',
    ):
        assert technical_field not in topology_node
    for business_field in (
        'nodeField(t("topology.detail.upstream")',
        'nodeField(t("topology.detail.input")',
        'nodeField(t("topology.detail.output")',
        'nodeField(t("topology.detail.authority")',
        'nodeField(t("topology.detail.execution")',
    ):
        assert business_field in topology_node


def test_oac_adaptation_is_a_preflight_add_on_not_a_second_product_flow() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "oac-adaptation.js").read_text(encoding="utf-8")
    css = (CONSOLE / "oac-adaptation.css").read_text(encoding="utf-8")

    assert len(re.findall(r'<button class="cockpit-tab(?: active)?"', html)) == 4
    assert 'id="tab-oac"' not in html
    assert 'id="cockpit-oac"' not in html
    assert html.index('id="oac-preflight-mount"') < html.index('class="journey"')
    assert 'preflightMount.replaceWith(byId("oac-adaptation"))' in javascript
    assert '<details class="oac-adaptation"' in html
    assert 'READY_FOR_SHADOW: "契约已准入并激活，可进入持续演化工作台"' in javascript
    assert (
        'READY_FOR_SHADOW: "Contract admitted and activated; the governed evolution Workspace is ready"'
        in javascript
    )
    assert 'continueToQuote: "进入报价工作台"' in javascript
    assert 'shadowOptional: "可选影子复验 · 业务启动门已放行"' in javascript
    assert 'button.dataset.action = "continue-quote"' in javascript
    assert 'window.OrgRebaseWorkspaceShell.navigate("quote")' in javascript
    primary_action = javascript.split("async function runPrimaryAction", 1)[1].split(
        'byId("oac-primary-action").addEventListener', 1
    )[0]
    assert primary_action.index('action === "continue-quote"') < primary_action.index("requestInFlight = true")
    assert "上下文已编译" not in javascript
    assert "Context compiled" not in javascript
    assert '<details class="oac-verification-evidence" id="oac-verification-evidence" hidden>' in html
    assert "高级审计证据" in html
    assert 'id="oac-product-outcomes"' in html
    for product_outcome in (
        "oac-product-business",
        "oac-product-gap",
        "oac-product-admission",
        "oac-product-shadow",
        "oac-product-effect",
    ):
        assert f'id="{product_outcome}"' in html
        assert f'setText("{product_outcome}"' in javascript
    assert html.index('id="oac-product-outcomes"') < html.index('id="oac-verification-evidence"')
    assert html.index('id="oac-verification-evidence"') < html.index('id="oac-source-grid"')
    assert 'id="oac-verification-evidence" open' not in html
    for endpoint in (
        "/api/workspace/oac-adaptation",
        "/api/workspace/oac-adaptation/agentic",
        "/api/workspace/oac-adaptation/agent-prepare",
        "/api/workspace/oac-adaptation/approve",
        "/api/workspace/oac-adaptation/execute-shadow",
    ):
        assert endpoint in javascript
    for root in ("DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"):
        assert root in javascript
    assert 'actorHeader: "X-OrgRebase-Actor"' in javascript
    assert "candidate_digest: candidateDigest" in javascript
    assert 'command_id: commandId("oac-admit")' in javascript
    assert "reviewGate.not_before" in javascript
    assert "reviewGate.not_before_epoch_ms" in javascript
    assert "reviewGate.digest" in javascript
    assert "approval_digest" in javascript
    assert "ZERO_EXTERNAL_EFFECTS" in javascript
    assert "canonical_target_writes" in javascript
    assert "accepted_mappings" in javascript
    assert "contextResidency" in javascript
    assert "sameRunLayers" in javascript
    assert "independent_verification" in javascript
    assert "checked_output_file_count" in javascript
    assert "topologyExact" in javascript
    assert "plannedDomainIds" in javascript
    assert "actualAgentTeamsDomainIds" in javascript
    assert "agentteamsPlanDigest" in javascript
    assert 'managerTitle: "确定性任务协调器 · 管理角色"' in javascript
    assert "organizationalDemand: raw.organizational_demand || {}" in javascript
    assert "resident.slice(0, 2)" not in javascript
    assert "projections.slice(0, 2)" not in javascript
    assert "独立复核 {outputs} 个输出 / {packs} 个输入文件 / 0 失败" in javascript
    assert "SHADOW_COMPLETED" in javascript
    assert "影子运行已完成 · 独立复核待执行" in javascript
    assert "影子运行与独立复核已通过" in javascript
    assert 'runIndependentVerification: "执行独立复验"' in javascript
    assert 'runIndependentVerification: "Run independent verification"' in javascript
    assert 'runningIndependentVerification: "正在执行独立复验…"' in javascript
    assert 'button.dataset.action = "execute-shadow"' in javascript
    assert "button.disabled = verificationPassed || !actionAllowed" in javascript
    assert "policyAllows(currentAdaptation, POLICY_ACTIONS.shadow)" in javascript
    assert 'state.independentVerification.status === "PASS"' in javascript
    assert 'agentVerified: "{suggestion}已验证"' in javascript
    assert 'auditTitle: "高级审计证据"' in javascript
    assert 'auditTitle: "Advanced audit evidence"' in javascript
    assert 'setTitle("oac-fact-run", state.adaptationRunId)' in javascript
    assert 'setTitle("oac-fact-contract", ...contractDigests)' in javascript
    assert 'state.consumptionReceiptDigest' in javascript
    assert 'state.activationBindingDigest' in javascript
    assert "digest.title = String(exactDigest)" in javascript
    assert "P0" not in javascript
    assert "P1" not in javascript
    for layer in ("SOURCE", "CONTEXT", "AGENTTEAMS", "TOOL", "SKILL", "OTLP", "CANDIDATE"):
        assert layer in javascript
    assert "OAC 定义智能体必须遵守的输入、权限、交接与准入规则" in html
    assert 'id="oac-business-gate"' in html
    assert 'data-gate-state="BLOCKED_PENDING_OAC"' in html
    assert "CONSUMED_BY_QUOTE_FORMATION" in javascript
    assert "倒计时只是界面提示" in html
    assert "服务端时间门" in html
    assert ".oac-adaptation-rail" in css
    assert ".oac-product-outcomes" in css
    assert ".oac-verification-evidence > summary" in css
    assert ".oac-verification-evidence[open] > summary" in css
    assert ".oac-source-grid" in css
    assert ".oac-context-model" in css


def test_oac_actions_are_backend_policy_driven_and_fail_closed() -> None:
    javascript = (CONSOLE / "oac-adaptation.js").read_text(encoding="utf-8")

    for mode in ("FROZEN_REPLAY", "OFFLINE_LOCAL", "LIVE_VERTEX", "UNKNOWN_SAFE"):
        assert mode in javascript
    for field in (
        "execution_policy",
        "mapping_source",
        "mapping_model_provider",
        "model_provider",
        "mapping_will_call_external_model",
        "shadow_will_call_external_model",
        "mapping_will_invoke_external_model",
        "shadow_will_invoke_external_model",
        "available_actions",
        "blocked_reasons",
    ):
        assert field in javascript
    assert 'prepare: "AGENT_PREPARE"' in javascript
    assert 'shadow: "EXECUTE_SHADOW"' in javascript
    assert "normalizeBlockedReasons" in javascript
    assert "Array.isArray(reason)" in javascript
    assert 'mode = POLICY_MODES.includes(declaredMode) ? declaredMode : "UNKNOWN_SAFE"' in javascript
    assert "availableActions.includes(action)" in javascript
    assert "if (policyAction && !policyAllows(currentAdaptation, policyAction))" in javascript
    assert "button.disabled = !actionAllowed" in javascript
    assert "验证接入候选" in javascript
    assert "运行本地候选适配" in javascript
    assert "运行新的 Vertex 候选映射" in javascript
    assert 'if (policy.mode === "FROZEN_REPLAY") return c.providerReceipt' in javascript
    assert 'if (policy.mode === "OFFLINE_LOCAL") return c.providerLocal' in javascript
    assert 'policy.mode === "LIVE_VERTEX"' in javascript
    assert 'liveProvider.includes("vertex") || liveProvider.includes("gemini")' in javascript
    assert "c.providerReceipt" in javascript


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the workspace-gate contract")
def test_required_oac_gate_blocks_form_in_the_ui_and_normalizes_fail_closed() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    oac_javascript = (CONSOLE / "oac-adaptation.js").read_text(encoding="utf-8")

    action_source = javascript.split("async function runPrimaryAction", 1)[1].split(
        "async function downloadJson", 1
    )[0]
    assert 'if (action.method === "open-oac")' in action_source
    assert 'if (action.method === "open-task-intake")' in action_source
    assert 'api("/api/workspace/form"' not in action_source
    assert "openOacWorkspaceGate();" in action_source
    assert 'method: "open-oac"' in javascript
    assert 'panel.open = true' in javascript
    assert 'panel.scrollIntoView({ behavior: "smooth", block: "start" })' in javascript
    assert 'window.addEventListener("orgrebase:oacadaptationchange"' in javascript
    assert 'window.addEventListener("orgrebase:workspacechange"' in oac_javascript
    assert 'new CustomEvent("orgrebase:workspacechange"' in (CONSOLE / "change-workbench.js").read_text()

    normalizer = "function normalizeOacWorkspaceGate" + javascript.split(
        "function normalizeOacWorkspaceGate", 1
    )[1].split("function openDetailPositions", 1)[0]
    node_script = f"""
{normalizer}
const cases = {{
  missing: normalizeOacWorkspaceGate(null),
  inconsistent: normalizeOacWorkspaceGate({{
    mode: "required", requires_oac_admission: true, form_allowed: true,
    status: "READY_TO_FORM", execution_run_id: "run:1",
  }}),
  blocked: normalizeOacWorkspaceGate({{
    mode: "required", requires_oac_admission: true, form_allowed: false,
    status: "BLOCKED_PENDING_OAC", execution_run_id: "run:1",
  }}),
  ready: normalizeOacWorkspaceGate({{
    mode: "required", requires_oac_admission: true, form_allowed: true,
    status: "READY_TO_FORM", execution_run_id: "run:1",
    activation_binding_digest: "sha256:binding",
  }}),
  consumed: normalizeOacWorkspaceGate({{
    mode: "required", requires_oac_admission: true, form_allowed: true,
    status: "CONSUMED_BY_QUOTE_FORMATION", execution_run_id: "run:1",
    activation_binding_digest: "sha256:binding",
    consumption_receipt_digest: "sha256:consumption",
  }}),
  consumedRestricted: normalizeOacWorkspaceGate({{
    mode: "required", requires_oac_admission: true, form_allowed: false,
    status: "CONSUMED_BY_QUOTE_FORMATION", execution_run_id: "run:1",
    activation_binding_digest: "sha256:binding",
    consumption_receipt_digest: "sha256:consumption",
  }}),
  optional: normalizeOacWorkspaceGate({{
    mode: "optional", requires_oac_admission: false, form_allowed: true,
    status: "READY_TO_FORM", execution_run_id: "run:1",
  }}),
  off: normalizeOacWorkspaceGate({{
    mode: "off", requires_oac_admission: false, form_allowed: true,
    status: "READY_TO_FORM", execution_run_id: "run:1",
  }}),
}};
console.log(JSON.stringify(cases));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    cases = json.loads(completed.stdout)
    assert cases["consumedRestricted"]["valid"] is True
    assert cases["consumedRestricted"]["formAllowed"] is False
    assert cases["consumedRestricted"]["status"] == "CONSUMED_BY_QUOTE_FORMATION"
    assert cases["off"]["valid"] is True
    assert cases["off"]["formAllowed"] is True
    assert cases["missing"] == {
        "valid": False,
        "mode": "unknown",
        "requiresOacAdmission": True,
        "formAllowed": False,
        "status": "BLOCKED_PENDING_OAC",
        "executionRunId": None,
        "activationBindingDigest": None,
        "consumptionReceiptDigest": None,
    }
    assert cases["inconsistent"]["valid"] is False
    assert cases["blocked"]["valid"] is True
    assert cases["ready"]["valid"] is True
    assert cases["consumed"]["valid"] is True
    assert cases["optional"]["valid"] is True


def test_oac_business_gate_projects_only_observed_causal_evidence() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "oac-adaptation.js").read_text(encoding="utf-8")
    css = (CONSOLE / "oac-adaptation.css").read_text(encoding="utf-8")

    assert 'id="oac-business-gate"' in html
    assert 'id="oac-product-business-detail"' in html
    assert "state.mappings.length" in javascript
    assert "resident.length" in javascript
    assert 'context.status === "READY"' in javascript
    assert "state.approvalDigest" in javascript
    assert 'state.sameRunLayers.includes("TOOL")' in javascript
    assert 'state.sameRunLayers.includes("SKILL")' in javascript
    assert 'byId("oac-product-business-detail").removeAttribute("title")' in javascript
    assert "gate.activationBindingDigest" not in javascript[javascript.index("function renderBusinessGate"):javascript.index("function render({ announceChange")]
    assert "gate.consumptionReceiptDigest" not in javascript[javascript.index("function renderBusinessGate"):javascript.index("function render({ announceChange")]
    assert '.oac-business-gate[data-gate-state="CONSUMED_BY_QUOTE_FORMATION"]' in css
    assert '.oac-product-outcomes strong { font-size: 13px' in css
    assert '.oac-adaptation[data-state="READY_FOR_SHADOW"]' in css
    assert '.oac-adaptation[data-state="READY_FOR_ORGREBASE"]' not in css


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the DOM refresh harness")
def test_oac_initial_render_reaches_refresh_and_renders_backend_execution_policy() -> None:
    payload = {
        "schema_version": "orgrebase.workspace.oac-agentic-adaptation.v1",
        "status": "NOT_STARTED",
        "adaptation": {"status": "PACK_OBSERVED"},
        "agent_mapping": {},
        "context_residency": {},
        "shadow_execution": {},
        "execution_policy": {
            "mode": "OFFLINE_LOCAL",
            "mapping_source": "UNAVAILABLE_OFFLINE_LOCAL",
            "mapping_model_provider": "vertex-ai",
            "model_provider": "ollama-local",
            "mapping_will_call_external_model": False,
            "shadow_will_call_external_model": False,
            "available_actions": [],
            "blocked_reasons": {
                "AGENT_PREPARE": ["OAC_AGENTIC_RUNTIME_NEW_VERTEX_MAPPING_BLOCKED"],
                "EXECUTE_SHADOW": ["OAC_AGENTIC_RUNTIME_LIVE_MAPPING_REQUIRED"],
            },
        },
        "workspace_gate": {
            "mode": "required",
            "requires_oac_admission": True,
            "form_allowed": False,
            "status": "BLOCKED_PENDING_OAC",
            "execution_run_id": "run:workspace:test",
            "activation_binding_digest": None,
            "consumption_receipt_digest": None,
        },
    }
    source_path = CONSOLE / "oac-adaptation.js"
    harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(__SOURCE_PATH__, "utf8");
const payload = __PAYLOAD__;
const nodes = new Map();

function makeNode(id = "") {
  const attributes = new Map();
  return {
    id,
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    textContent: "",
    title: "",
    disabled: false,
    hidden: false,
    open: false,
    listeners: {},
    setAttribute(name, value) {
      attributes.set(name, String(value));
      if (name === "title") this.title = String(value);
    },
    removeAttribute(name) {
      attributes.delete(name);
      if (name === "title") this.title = "";
    },
    addEventListener(type, listener) { this.listeners[type] = listener; },
    replaceWith() {},
    replaceChildren() {},
    append() {},
  };
}

function byId(id) {
  if (!nodes.has(id)) nodes.set(id, makeNode(id));
  return nodes.get(id);
}

const document = {
  documentElement: { lang: "en" },
  getElementById: byId,
  querySelectorAll() { return []; },
  createElement() { return makeNode(); },
};
const window = {
  OrgRebaseClient: { fetch: (...args) => fetch(...args), readBody: response => response.json(),
    session: () => ({mode:"local",authentication_required:false}) },
  crypto: { randomUUID: () => "test-uuid" },
  addEventListener() {},
  dispatchedEvents: [],
  dispatchEvent(event) { this.dispatchedEvents.push(event); },
  setInterval,
  clearInterval,
};
class CustomEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.detail = options.detail;
  }
}
let fetchCalls = 0;
const requests = [];
async function fetch(url, options = {}) {
  fetchCalls += 1;
  requests.push({ url, method: options.method || "GET" });
  if (url.endsWith("/prepare")) {
    payload.status = "OWNER_REVIEW_PENDING";
    payload.adaptation = {
      status: "OWNER_REVIEW_PENDING",
      owner_ref: "actor:owner",
      candidate_digest: "sha256:structured-candidate",
      owner_review_summary_digest: "sha256:structured-summary",
      owner_review_summary: {
        digest: "sha256:structured-summary",
        candidate_mapping_set_digest: "sha256:structured-candidate",
        component_reviews: ["DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"].map((kind) => ({ kind })),
      },
      candidate_mappings: ["DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"].map((kind) => ({
        component_kind: kind, source_root_ref: `source:${kind.toLowerCase()}`,
      })),
      review_remaining_ms: 0,
    };
    payload.agent_mapping = { accepted_mappings: [] };
    payload.execution_policy.available_actions = [];
    return { ok: true, status: 200, async json() { return payload.adaptation; } };
  }
  return { ok: true, status: 200, async json() { return payload; } };
}

vm.runInNewContext(source, {
  window,
  document,
  CustomEvent,
  fetch,
  console,
  Date,
  Math,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
});

setImmediate(async () => {
  const root = byId("oac-adaptation");
  const intro = byId("oac-adaptation-intro-body").textContent;
  const boundary = byId("oac-adaptation-boundary").textContent;
  const action = byId("oac-primary-action");
  const controlNote = byId("oac-control-note");
  const businessGate = byId("oac-business-gate");
  assert.equal(fetchCalls, 1, "the initial fail-closed render must not abort the real refresh");
  assert.equal(root.dataset.state, "NOT_STARTED");
  assert.match(intro, /defines the inputs, authority, handoff, and admission rules/i);
  assert.match(boundary, /Current mode: Local interactive demo/);
  assert.match(boundary, /candidate source: no new mapping available in local mode/);
  assert.match(boundary, /No new external-model call will be made/);
  assert.match(boundary, /Only an observed model receipt is presented as a real run/);
  assert.match(boundary, /onboarding Agent proposes only/);
  assert.match(boundary, /designated owner decide whether to activate it/);
  assert.doesNotMatch(boundary, /Policy not verified/);
  assert.equal(action.disabled, true, "empty backend available_actions must remain fail-closed");
  assert.match(action.title, /local interactive demo does not expose this model action/i);
  assert.equal(businessGate.dataset.gateState, "BLOCKED_PENDING_OAC");
  assert.equal(byId("oac-product-business").textContent, "QUOTE NOT RELEASED");
  assert.match(controlNote.textContent, /local interactive demo does not expose this model action/i);
  assert.equal(controlNote.title, "");
  assert.equal(window.dispatchedEvents.length, 1);
  assert.equal(window.dispatchedEvents[0].type, "orgrebase:oacadaptationchange");
  assert.equal(window.dispatchedEvents[0].detail.workspaceGate.formAllowed, false);

      payload.status = "READY_FOR_SHADOW";
      const ownerSummaryDigest = "sha256:owner-review-summary";
      payload.adaptation.candidate_digest = "sha256:retained-candidate";
      payload.adaptation.candidate_mappings = ["DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"].map((kind) => ({
        component_kind: kind, source_root_ref: `source:${kind.toLowerCase()}`,
      }));
      payload.adaptation.owner_review_summary = {
        digest: ownerSummaryDigest,
        candidate_mapping_set_digest: "sha256:retained-candidate",
        component_reviews: ["DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"].map((kind) => ({ kind })),
      };
      payload.adaptation.review_gate = { owner_review_summary_digest: ownerSummaryDigest };
      payload.adaptation.approval = {
        digest: "sha256:approval",
        owner_review_summary_digest: ownerSummaryDigest,
        acknowledgements: [
          "REVIEWED_SOURCE_TO_CONTRACT_SUMMARY",
          "ACCEPTED_DECLARED_UNKNOWNS",
          "UNDERSTAND_NO_BUSINESS_APPROVAL",
        ],
      };
      payload.adaptation.adapter_capsule = {
        digest: "sha256:capsule",
        owner_review_summary_digest: ownerSummaryDigest,
      };
      payload.workspace_gate = {
    mode: "required",
    requires_oac_admission: true,
    form_allowed: true,
    status: "READY_TO_FORM",
    execution_run_id: "run:workspace:test",
    activation_binding_digest: "sha256:activation",
    consumption_receipt_digest: null,
  };
  payload.execution_policy = {
    mode: "FROZEN_REPLAY",
    mapping_source: "RETAINED_VERTEX_RECEIPT",
    mapping_model_provider: "vertex-ai",
    model_provider: "ollama-local",
    mapping_will_call_external_model: false,
    shadow_will_call_external_model: false,
    available_actions: ["AGENT_PREPARE"],
    blocked_reasons: {
      EXECUTE_SHADOW: ["OAC_AGENTIC_RUNTIME_FROZEN_SHADOW_RECEIPT_REQUIRED"],
    },
  };
  await byId("oac-refresh").listeners.click();
  const replayBoundary = byId("oac-adaptation-boundary").textContent;
  assert.match(replayBoundary, /Current mode: Frozen evidence replay/);
  assert.match(replayBoundary, /candidate source: sealed mapping receipt/);
  assert.match(replayBoundary, /No new external-model call will be made/);
  assert.match(replayBoundary, /Only an observed model receipt is presented as a real run/);
  assert.match(replayBoundary, /onboarding Agent proposes only/);
  assert.match(replayBoundary, /designated owner decide whether to activate it/);
  assert.doesNotMatch(replayBoundary, /Policy not verified|Local model advisory|Live Vertex model advisory/);
  assert.equal(businessGate.dataset.gateState, "READY_TO_FORM");
  assert.equal(byId("oac-product-business").textContent, "QUOTE MAY BE FORMED");
  assert.equal(window.dispatchedEvents.length, 2);
  assert.equal(window.dispatchedEvents[1].detail.workspaceGate.formAllowed, true);

  payload.workspace_gate.status = "CONSUMED_BY_QUOTE_FORMATION";
  payload.workspace_gate.consumption_receipt_digest = "sha256:consumption";
  await byId("oac-refresh").listeners.click();
  assert.equal(businessGate.dataset.gateState, "CONSUMED_BY_QUOTE_FORMATION");
  assert.equal(byId("oac-product-business").textContent, "THIS QUOTE CONSUMED OAC");
  assert.match(byId("oac-product-business-detail").textContent, /persisted the activation binding and consumption receipt/i);
  assert.equal(window.dispatchedEvents.length, 3);
  assert.equal(window.dispatchedEvents[2].detail.workspaceGate.consumptionReceiptDigest, "sha256:consumption");

  payload.workspace_gate.mode = "optional";
  payload.workspace_gate.requires_oac_admission = false;
  await byId("oac-refresh").listeners.click();
  assert.equal(businessGate.dataset.gateState, "CONSUMED_BY_QUOTE_FORMATION");
  assert.equal(byId("oac-product-business").textContent, "THIS QUOTE CONSUMED OAC");
  assert.match(byId("oac-product-business-detail").textContent, /precisely bound and consumed OAC/i);
  assert.match(byId("oac-product-business-detail").textContent, /does not require every task to use OAC/i);
  assert.doesNotMatch(byId("oac-product-business").textContent, /OPTIONAL VALIDATION ADD-ON/i);
  assert.equal(window.dispatchedEvents.length, 4);

  payload.status = "NOT_STARTED";
  payload.adaptation = { status: "PACK_OBSERVED" };
  payload.workspace_gate = {
    mode: "required", requires_oac_admission: true, form_allowed: false,
    status: "BLOCKED_PENDING_OAC", execution_run_id: "run:workspace:structured",
  };
  payload.execution_policy = {
    mode: "OFFLINE_LOCAL", mapping_source: "STRUCTURED_MATERIALS",
    mapping_model_provider: "none", model_provider: "ollama-local",
    mapping_will_call_external_model: false, shadow_will_call_external_model: false,
    available_actions: ["STRUCTURED_PREPARE"], blocked_reasons: {},
  };
  await byId("oac-refresh").listeners.click();
  assert.equal(action.disabled, false);
  assert.equal(action.dataset.action, "structured-prepare");
  assert.match(action.textContent, /configured materials/i);
  assert.match(byId("oac-adaptation-boundary").textContent, /no mapping-model call/i);
  await action.listeners.click();
  assert.deepEqual(requests.slice(-2), [
    { url: "/api/workspace/oac-adaptation/prepare", method: "POST" },
    { url: "/api/workspace/oac-adaptation/agentic", method: "GET" },
  ]);
  assert.equal(root.dataset.state, "OWNER_REVIEW_PENDING");
  assert.equal(byId("oac-product-agent").textContent, "Candidate compiled from configured materials");
  assert.match(byId("oac-product-agent-detail").textContent, /no mapping-model call/i);
  assert.notEqual(byId("oac-product-validation").textContent, "Awaiting candidate");
  assert.equal(action.dataset.action, "approve");
  assert.equal(action.disabled, true, "preparing materials must not bypass owner acknowledgements");
  assert.equal(businessGate.dataset.gateState, "BLOCKED_PENDING_OAC");
  assert.equal(requests.filter(item => item.method === "POST").length, 1);
  const ackIds = ["oac-ack-summary", "oac-ack-unknowns", "oac-ack-boundary"];
  assert.ok(ackIds.every(id => byId(id).checked === false), "new subjects must not inherit old recorded consent");
  ackIds.forEach(id => { byId(id).checked = true; });
  await byId("oac-refresh").listeners.click();
  assert.equal(action.disabled, false, "refreshing the same subject preserves the user's selections");
  payload.adaptation.owner_review_summary.digest = "sha256:revised-summary";
  payload.adaptation.owner_review_summary_digest = "sha256:revised-summary";
  await byId("oac-refresh").listeners.click();
  assert.equal(action.disabled, true, "a revised summary requires new acknowledgements");
  assert.ok(ackIds.every(id => byId(id).checked === false));
});
"""
    harness = harness.replace("__SOURCE_PATH__", json.dumps(str(source_path)))
    harness = harness.replace("__PAYLOAD__", json.dumps(payload))

    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", harness],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'oac-adaptation.js?v=0.4.0-14' in (CONSOLE / "index.html").read_text(encoding="utf-8")


def test_console_uses_business_event_scope_and_discloses_global_oac_suffix() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert "function projectedEventScopes(state)" in javascript
    assert 'quote: scopes.quote_business || {' in javascript
    assert 'definition: "NO_QUOTE_BUSINESS_EVENTS_OBSERVED"' in javascript
    assert "scopes.quote_business || legacy" not in javascript
    assert "scopes.workspace_global || legacy" in javascript
    assert "scopes.oac_adaptation || {}" in javascript
    assert "const chain = projectedEventScopes(state).quote" in javascript
    assert "const eventScopes = projectedEventScopes(state)" in javascript
    assert 't("operations.events.scoped"' in javascript
    assert '"operations.events.scoped": "报价业务链 {quoteStatus} · {quote} 个事件；工作区全局链 {global} 个，其中 OAC 治理 {oac} 个"' in javascript
    assert '"governance.events": "{count} 个报价业务事件 · 完整性通过"' in javascript


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the event-scope contract")
def test_oac_change_event_refreshes_authoritative_workspace_event_scopes() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    projection_source = "function eventCount" + javascript.split("function eventCount", 1)[1].split(
        "// Machine enums remain authoritative", 1
    )[0]
    state = {
        "event_chain": {"status": "PASS", "events": 14},
        "event_scopes": {
            "quote_business": {"status": "PASS", "events": 12},
            "workspace_global": {"status": "PASS", "events": 14},
            "oac_adaptation": {"status": "PASS", "events": 2},
        },
    }
    node_script = f"""
{projection_source}
const state = {json.dumps(state)};
const scopes = projectedEventScopes(state);
console.log(JSON.stringify({{
  quote: eventCount(scopes.quote),
  global: eventCount(scopes.global),
  oac: eventCount(scopes.oac),
}}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"quote": 12, "global": 14, "oac": 2}

    refresh_bridge = javascript.split("function refreshChangedWorkspace()", 1)[1].split(
        "async function runPrimaryAction", 1
    )[0]
    assert 'window.addEventListener("orgrebase:oacadaptationchange"' in refresh_bridge
    assert "refreshState().catch" in refresh_bridge
    assert 'api("/api/workspace/state")' in javascript
    for forbidden in ("setInterval", "event_scopes =", "setLanguage(", ".click("):
        assert forbidden not in refresh_bridge

    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    assert 'app.js?v=0.4.0-15' in html
    assert 'oac-adaptation.js?v=0.4.0-14' in html
    assert 'workspace-shell.js?v=0.4.0-13' in html


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the event-scope contract")
def test_missing_quote_scope_never_inherits_a_passing_global_chain() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    projection_source = "function eventCount" + javascript.split("function eventCount", 1)[1].split(
        "// Machine enums remain authoritative", 1
    )[0]
    state = {
        "event_chain": {"status": "PASS", "events": 7},
        "event_scopes": {
            "workspace_global": {"status": "PASS", "events": 7},
            "oac_adaptation": {"status": "PASS", "events": 2},
        },
    }
    node_script = f"""
{projection_source}
const state = {json.dumps(state)};
const scopes = projectedEventScopes(state);
console.log(JSON.stringify({{
  quoteStatus: scopes.quote.status,
  quoteEvents: eventCount(scopes.quote),
  globalStatus: scopes.global.status,
}}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "quoteStatus": "NOT_OBSERVED",
        "quoteEvents": 0,
        "globalStatus": "PASS",
    }


def test_console_model_suggestion_labels_follow_provider_evidence() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert "function modelSuggestion(provider, evidenceClass)" in javascript
    assert 'normalizedEvidence.includes("LIVE_VERTEX") || normalizedEvidence === "LIVE_MODEL"' in javascript
    assert 'normalizedProvider.includes("ollama") || normalizedProvider.includes("local")' in javascript
    assert 'return t("modelSuggestion.receipt")' in javascript
    assert '"modelSuggestion.vertexLive": "真实 Vertex 模型建议"' in javascript
    assert '"modelSuggestion.local": "本地模型建议"' in javascript
    assert '"modelSuggestion.receipt": "模型建议回执"' in javascript
    assert "suggestion: reviewerSuggestion" in javascript


def test_console_renders_directed_agent_topologies_and_terminal_change_receipts() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    css = (CONSOLE / "styles.css").read_text(encoding="utf-8")

    assert 'id="active-agent-topology"' in html
    assert 'id="retained-agent-topology"' in html
    assert 'id="active-topology-boundary"' in html
    assert 'id="retained-topology-boundary"' in html
    assert 'id="active-native-lifecycle"' in html
    assert 'id="active-native-lifecycle-rail"' in html
    assert "AgentTeams 已接收 ≠ 控制面已准入 ≠ 人工已批准 ≠ 规范状态已写入" in html
    assert "控制面任务协调器、领域智能体、审查智能体" in html
    assert 'const toolNode = {' not in javascript
    assert 'const skillNode = {' not in javascript
    assert 'role: t("topology.role.tool")' not in javascript
    assert 'role: t("topology.role.skill")' not in javascript
    assert 'kind: "Tool"' in javascript
    assert 'kind: "Skill"' in javascript
    assert 'nodes: [reviewerOneNode, financeTwoNode, reviewerTwoNode]' in javascript
    assert 'nodes: [...(gtmComposeNode ? [gtmComposeNode] : []), formationNode]' in javascript
    assert "renderGoldenNativeLifecycle" in javascript
    assert 'task.role === "DOMAIN_WORKER" || task.role === "REVIEWER"' in javascript
    assert "agentteams_action_count" in javascript
    assert "project_terminal_state" in javascript
    assert 'status: t("topology.status.managerRole")' in javascript
    assert "NOT AN AGENTTEAMS NATIVE TASK" in javascript
    assert "不伪装为原生管理任务" in javascript
    assert "goldenTaskNode(managerTask, runs, handoffs" not in javascript
    assert 't("topology.status.atReceived")' in javascript
    assert 't("topology.status.atReceivedAbstain")' in javascript
    assert 't("topology.status.toolRecovered")' in javascript
    assert "冲突重派、进程恢复与补偿属于独立验证运行" in html
    assert '"topology.role.manager": "Control-plane task coordinator"' in javascript
    assert '"topology.role.manager": "控制面任务协调器"' in javascript
    assert 'role: t("topology.role.workerGeneric"' in javascript
    for actor in ("product-steward", "legal-steward", "finance-steward", "gtm-steward"):
        assert actor in javascript
    assert "CONTROL ACCEPTANCE · NO INDEPENDENT REVIEWER" in javascript
    assert "NO INDEPENDENT REVIEWER" in html or "NO INDEPENDENT REVIEWER" in javascript
    assert "auditor:workspace-reviewer" not in javascript
    for field in (
        "depends_on",
        "input refs",
        "input digest",
        "output digest",
        "model",
        "model provider",
        "provider request",
        "model authority",
        "tool",
        "skill",
        "trace",
        "candidate-only",
        "target writes",
    ):
        assert field in javascript
    assert "state.competition_evidence" in javascript
    assert "reviewer.model_provider || competition.model_provider" in javascript
    assert 'reviewer.model_version || "NOT_OBSERVED"' in javascript
    assert 't("collaboration.active.model"' in javascript
    assert 't(oacExecutionBound ? "topology.boundary.activeOac" : "topology.boundary.active"' in javascript
    assert "CONTEXT ONLY · NO WORKER RUN OBSERVED" in javascript
    assert "NOT_OBSERVED" in javascript
    assert "PINNED IN-PROCESS · NOT LIVE DISTRIBUTED" in javascript
    assert ".agent-topology" in css
    assert ".topology-node summary:focus-visible" in css
    assert ".native-lifecycle-rail" in css
    assert ".native-lifecycle-authority" in css


def test_fresh_collaboration_waits_for_real_team_formation_and_hides_receipt_ids_by_default() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    css = (CONSOLE / "styles.css").read_text(encoding="utf-8")
    active = javascript.split("function renderActiveCollaboration", 1)[1].split(
        "function experienceRemaining", 1
    )[0]
    topology_node = javascript.split("function topologyNode", 1)[1].split(
        "function topologyStage", 1
    )[0]

    assert "const teamObserved = coalitionReady || tasks.length > 0 || agentRuns.length > 0 || handoffs.length > 0;" in active
    assert active.index("if (!teamObserved)") < active.index("const coordinatorTask")
    assert 'classList.add("empty-topology")' in active
    assert 'class="topology-empty-state"' in active
    assert 't("collaboration.empty.title")' in active
    assert '<details class="topology-technical-audit">' in topology_node
    assert 't("topology.detail.technicalAudit")' in topology_node
    assert '${node.invocation && node.invocation.receipt ? nodeField(' not in topology_node
    assert ".agent-topology.empty-topology" in css
    assert ".topology-technical-audit" in css

    assert 'id="selective-change-card"' in html
    assert "协作工作稿 → 币种确认稿的选择性变化" in html
    assert "renderSelectiveChangeCard" in javascript
    for metric in (
        "rebuilt",
        "preserved",
        "unknown",
        "false invalidations",
        "unauthorized disclosures",
        "approval digest",
    ):
        assert metric in javascript


def test_console_visualizes_one_run_without_merging_truth_lanes() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert "单一场景 · 运行实况" in html
    assert "ONE SCENARIO · LIVE RUN" in javascript
    assert "页面只读展示实际进度" in html
    assert "不改写业务状态" in html
    assert "当前业务运行" in html
    assert "独立机制验证档案" in html
    assert "异常处理规则" in html
    assert "静态处理规则 · 运行收据见本页独立验证档案" in html
    for marker in ("403", "409", "报价包 / 数据库不匹配", "SIGKILL"):
        assert marker in html
    assert "独立受控本地运行 · 重试意图 / 采纳已提交结果" in html
    assert "当前业务运行已完成本地闭环" in javascript
    assert "独立本地运维运行已验证 2 次 SIGKILL" in javascript
    assert "自主分布式 AgentTeams 的跨进程恢复属于生产验证范围" in javascript
    for renderer in (
        "renderActiveEvidenceSpine",
        "renderActiveActionLedger",
        "renderActiveCollaboration",
        "renderGoldenCollaboration",
        "renderRetainedEvidence",
        "renderValueAndResponsibility",
        "renderOperations",
    ):
        assert renderer in javascript
    assert "current_pack_pilot_run" not in html
    assert "它不是当前报价任务的第二次执行" in html
    assert "CONTROLLED_LOCAL_AGENTTEAMS" in javascript
    assert "分布式生产 AgentTeams 与真实企业 ROI 仍需企业环境验收" in javascript


def test_console_closes_business_data_authority_and_dynamic_formation_views() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    css = (CONSOLE / "styles.css").read_text(encoding="utf-8")

    assert 'data-i18n="cockpit.tab.scene">业务与数据</button>' in html
    assert '"cockpit.tab.scene": "Business & Data"' in javascript
    for element_id in (
        "business-change-board",
        "business-change-rounds",
        "business-change-total",
        "business-change-next",
        "authority-ladder-steps",
        "formation-proof-card",
        "formation-agent-topology",
    ):
        assert f'id="{element_id}"' in html

    assert "function changeProjection" in javascript
    assert "function renderBusinessChangeBoard" in javascript
    for projected_fact in (
        "metrics.work_items_rebased",
        "metrics.bounded_unaffected",
        "metrics.unknown",
        "metrics.false_invalidations",
        "metrics.unauthorized_disclosures",
        "review_wait_satisfied",
        "outcome.workspace_rebase_receipt",
    ):
        assert projected_fact in javascript
    assert "两轮运行合计，不是去重后对象数" in javascript
    assert 'projection.receiptComplete || projection.previewImpactValid' in javascript
    assert 'data-phase="${escapeHtml(projection.impactPhase)}"' in javascript
    assert 'projection.receiptComplete ? `<div><dt>${escapeHtml(t("businessChange.falseInvalidations"))}' in javascript
    assert 'next.dataset.routeTarget = completeRun ? "validation" : "quote"' in javascript

    assert "function renderAuthorityLadder" in javascript
    assert 'competition.project_terminal_state === "completed"' in javascript
    assert 'row.status === "LOCKED"' in javascript
    assert 'finalReview[1].verdict === "PASS"' in javascript
    assert "reviewer.target_writes === 0" in javascript
    assert "reviewerAuthority.reviewer_target_writes === 0" in javascript
    assert 'Number(reviewer.target_writes) === 0' not in javascript
    assert 'Number(reviewerAuthority.reviewer_target_writes) === 0' not in javascript
    assert 'preview.kind === eventId' in javascript
    assert 'binding.change_kind === eventId' in javascript
    assert 'outcomeEnvelope.kind === eventId' in javascript
    assert 'baseReceipt.workflow_run_id === runId' in javascript
    assert 'row.status === "APPROVED"' in javascript
    assert 'workspaceReceipt.status === "COMPLETED"' in javascript
    assert "AgentTeams 已完成" in javascript
    assert "候选已准入" in javascript
    assert "审查智能体已验收" in javascript
    assert "人工已批准" in javascript
    assert "规范状态已写入" in javascript
    assert '.authority-ladder-steps { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); }' in css

    assert "function renderFormationTopology" in javascript
    assert "domainTasks.map" in javascript
    assert "reviewer.depends_on" in javascript
    assert 'renderDirectedTopology("formation-agent-topology"' in javascript
    assert "权威边界、冲突与结果隔离" in html
    assert "只证明控制语义 · 不重复当前业务流程" in html
    assert '<details class="retained-control-semantics">' in html
    assert "[formationNode, ...lifecycleNodes]" not in javascript

    assert '<details class="technical-evidence-details"' in html
    assert '<details class="ledger-shell retained-ledger-shell">' in html
    assert "运行编号、候选链路与同运行操作台账" in html
    assert ".business-change-board" in css
    assert ".authority-ladder-steps" in css
    assert ".formation-proof-card" in css
    assert ".technical-evidence-details" in css


def test_console_visualizes_the_backend_enterprise_data_lineage_without_frontend_truth_fallback() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    css = (CONSOLE / "styles.css").read_text(encoding="utf-8")

    required_ids = (
        "enterprise-data-journey",
        "enterprise-data-journey-rail",
        "enterprise-data-journey-source",
        "enterprise-data-journey-oac",
        "enterprise-data-journey-context",
        "enterprise-data-journey-quotes",
        "enterprise-data-journey-changes",
    )
    for element_id in required_ids:
        assert f'id="{element_id}"' in html
    assert (
        html.index('id="oac-adaptation"')
        < html.index('id="enterprise-data-journey"')
        < html.index('id="business-change-board"')
    )
    assert "function renderEnterpriseDataJourney" in javascript
    assert "renderEnterpriseDataJourney(state);" in javascript
    assert 'section.dataset.lineageStatus = lineageStatus' in javascript
    assert 'section.dataset.readModelTargetWrites = String' in javascript
    assert 'data-change-kind="${escapeHtml(kind' in javascript
    assert 'source.status === "ADMITTED_AND_MATCHED"' in javascript
    assert 'sourceAdmissionVerdict === "ADMITTED"' in javascript
    assert 'runtimeProjectionVerdict === "MATCH"' in javascript
    assert "bindings.every((binding)" in javascript
    assert "const oacReady =" in javascript
    assert 'change.preview_evidence_status === "VMRC_EXACT_BOUND"' in javascript
    assert 'enterpriseDataJourneyMetric(previewMetrics, "affected_hard")' in javascript
    assert 'enterpriseDataJourneyMetric(previewMetrics, "human_review")' in javascript

    renderer = javascript.split("function renderEnterpriseDataJourney", 1)[1].split(
        "function renderBusinessChangeBoard", 1
    )[0]
    assert "state && state.enterprise_data_lineage" in renderer
    assert 'lineage && lineage.status || "WAITING_FOR_FORMATION"' in renderer
    assert 'lineage && lineage.read_model_target_writes' in renderer
    for forbidden_fallback in (
        "state.quote",
        "state.changes",
        "state.enterprise_seed_profile",
        "state.enterprise_seed_source_admission",
        "state.enterprise_seed_runtime_projection",
        "raw_private_value",
        "lineage.run_id",
        "lineage.digest",
        "Blue Harbor",
        "2026-10-01",
        "2026-10-15",
        "USD",
        "EUR",
    ):
        assert forbidden_fallback not in renderer

    for selector in (
        ".enterprise-data-journey",
        ".enterprise-data-journey-rail",
        ".enterprise-data-source-values",
        ".enterprise-data-oac-bindings",
        ".enterprise-data-context-actors",
        ".enterprise-data-quote-versions",
        ".enterprise-data-journey-changes",
    ):
        assert selector in css


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js is required for lineage gate checks",
)
def test_enterprise_data_journey_completion_gates_fail_closed_on_non_exact_evidence() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    renderers = "function renderEnterpriseDataSource" + javascript.split(
        "function renderEnterpriseDataSource", 1
    )[1].split("function renderEnterpriseDataContext", 1)[0]
    node_script = f"""
const DATA_JOURNEY_COMPONENT_KEYS = Object.freeze({{
  DOMAIN: "domain", KNOWLEDGE: "knowledge", AUTHORITY: "authority",
  CAPABILITY: "capability", DEPENDENCY: "dependency",
}});
const DATA_JOURNEY_SOURCE_HINTS = [];
const DATA_JOURNEY_AUTHORITY_HINTS = [];
const states = {{}};
function setEnterpriseDataJourneyStep(id, stepState) {{ states[id] = stepState; }}
function t(key) {{ return key; }}
function escapeHtml(value) {{ return String(value); }}
function dataJourneySlotLabel(value) {{ return String(value || "NOT_OBSERVED"); }}
function displayToken(value) {{ return String(value); }}
function dataJourneySensitivityLabel(value) {{ return String(value || "NOT_OBSERVED"); }}
function dataJourneyObserved(value) {{ return String(value || "NOT_OBSERVED"); }}
function dataJourneyReferenceLabel(value) {{ return String(value || "NOT_OBSERVED"); }}
function dataJourneyVerdict(value) {{ return String(value || "NOT_OBSERVED"); }}
function dataJourneyComponentLabel(value) {{ return String(value || "NOT_OBSERVED"); }}
{renderers}

const sourceValue = {{ slot_id: "product_plan", domain_id: "product", value: "Enterprise" }};
renderEnterpriseDataSource({{ status: "ADMITTED_AND_MATCHED", values: [sourceValue] }});
const exactSource = states["enterprise-data-journey-source"];
renderEnterpriseDataSource({{ status: "UNOBSERVED", values: [sourceValue] }});
const wrongSourceStatus = states["enterprise-data-journey-source"];
renderEnterpriseDataSource({{ status: "ADMITTED_AND_MATCHED", values: [] }});
const emptySource = states["enterprise-data-journey-source"];

const kinds = Object.keys(DATA_JOURNEY_COMPONENT_KEYS);
const exactBindings = kinds.map((kind) => ({{
  kind,
  source_admission_verdict: "ADMITTED",
  runtime_projection_status: "MATCH",
}}));
    const exactOac = {{
      source_admission_verdict: "ADMITTED",
      runtime_projection_verdict: "MATCH",
      activation_status: "CONSUMED_BY_QUOTE_FORMATION",
      execution_binding_status: "OAC_BOUND_EXECUTION_PLAN_REALIZED",
      topology_match: true,
      selected_domain_ids: ["product", "legal", "finance", "gtm"],
      component_bindings: exactBindings,
    }};
renderEnterpriseDataOac(exactOac);
const exactOacState = states["enterprise-data-journey-oac"];
renderEnterpriseDataOac({{ ...exactOac, source_admission_verdict: "UNKNOWN" }});
const wrongOverall = states["enterprise-data-journey-oac"];
renderEnterpriseDataOac({{
  ...exactOac,
  component_bindings: exactBindings.map((binding, index) => (
    index === 0 ? {{ ...binding, runtime_projection_status: "MISMATCH" }} : binding
  )),
}});
const wrongBinding = states["enterprise-data-journey-oac"];
    renderEnterpriseDataOac({{ ...exactOac, activation_status: "READY_TO_FORM" }});
    const notConsumed = states["enterprise-data-journey-oac"];
    renderEnterpriseDataOac({{ ...exactOac, execution_binding_status: "NOT_OBSERVED" }});
    const noSameRunBinding = states["enterprise-data-journey-oac"];

console.log(JSON.stringify({{
  exactSource, wrongSourceStatus, emptySource,
      exactOacState, wrongOverall, wrongBinding, notConsumed, noSameRunBinding,
}}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    states = json.loads(completed.stdout)
    assert states == {
        "exactSource": "complete",
        "wrongSourceStatus": "waiting",
        "emptySource": "waiting",
        "exactOacState": "complete",
        "wrongOverall": "active",
        "wrongBinding": "active",
        "notConsumed": "active",
        "noSameRunBinding": "active",
    }


def test_console_renders_every_persisted_stage_and_explicit_owner() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    for stage in (
        "EMPTY",
        "CURRENT",
        "PREVIEWED",
        "APPROVED",
    ):
        assert stage in javascript
    assert "state.actions && state.actions.owner_id" in javascript
    assert "actionForState(currentState)" in javascript
    assert "preview_digest" in javascript
    assert "approval_digest" in javascript


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the preview-kind contract")
def test_command_copy_never_reuses_a_stale_preview_delta_for_another_change_kind() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    projection_source = "function activePreview" + javascript.split("function activePreview", 1)[1].split(
        "function oacGateActionForState", 1
    )[0]
    node_script = f"""
{projection_source}
const launchDelta = {{ object_id: "claim:product.launch_date", base_value: "2026-10-01", proposed_value: "2026-10-15" }};
const currencyDelta = {{ object_id: "policy:finance.currency", base_value: "USD", proposed_value: "EUR" }};
const cases = {{
  staleLaunchForCurrency: previewDeltaForKind({{
    latest_preview: {{ kind: "launch_date", bundle: {{
      change_spec: {{ object_id: "claim:product.launch_date" }},
      change_set: {{ deltas: [launchDelta] }},
    }} }},
  }}, "currency"),
  mislabeledCurrency: previewDeltaForKind({{
    latest_preview: {{ kind: "currency", bundle: {{
      change_spec: {{ object_id: "claim:product.launch_date" }},
      change_set: {{ deltas: [launchDelta] }},
    }} }},
  }}, "currency"),
  validCurrency: previewDeltaForKind({{
    latest_preview: {{ kind: "currency", bundle: {{
      change_spec: {{ object_id: "policy:finance.currency" }},
      change_set: {{ deltas: [currencyDelta] }},
    }} }},
  }}, "currency"),
}};
console.log(JSON.stringify(cases));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    cases = json.loads(completed.stdout)
    assert cases["staleLaunchForCurrency"] is None
    assert cases["mislabeledCurrency"] is None
    assert cases["validCurrency"] == {
        "object_id": "policy:finance.currency",
        "base_value": "USD",
        "proposed_value": "EUR",
    }


def test_console_exposes_truthful_form_progress_oac_checking_and_experience_navigation() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    for chinese_copy in (
        "正在校验 · OAC 业务启动门",
        "校验完成前保持关闭；页面不提前判定已准入或未准入。",
        "固定版本 AgentTeams 正在形成内部工作稿候选",
        "当前尚未观察到终态",
        "候选发布决策者 · 显式人工权威",
    ):
        assert chinese_copy in javascript
    for english_copy in (
        "CHECKING · OAC BUSINESS START GATE",
        "No terminal state has been observed yet.",
        "Review experience candidate",
    ):
        assert english_copy in javascript

    assert "let oacWorkspaceGateObserved = false" in javascript
    assert "oacWorkspaceGateObserved = true" in javascript
    assert 'method: "open-experience"' in javascript
    assert 'window.OrgRebaseWorkspaceShell.navigate("skills")' in javascript
    assert 'activateCockpitView("skills")' in javascript
    assert 'panel.scrollIntoView({ behavior: "smooth", block: "start" })' in javascript
    assert 'method: "open-task-intake"' in javascript
    assert "openTaskIntake();" in javascript
    assert "focusTaskIntake" in javascript
    assert 'api("/api/workspace/form"' not in javascript


def test_lane_note_allows_long_runtime_identifiers_to_wrap_on_small_screens() -> None:
    css = (CONSOLE / "styles.css").read_text(encoding="utf-8")

    assert ".lane-note > * { min-width: 0; }" in css
    assert ".lane-note strong { overflow-wrap: anywhere;" in css
    assert ".lane-note small { color: var(--muted); font-size: 10px; line-height: 1.45; overflow-wrap: anywhere; }" in css


def test_workspace_shell_pages_existing_product_and_uses_authoritative_task_intake() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    shell_css = (CONSOLE / "workspace-shell.css").read_text(encoding="utf-8")

    assert 'workspace-shell.css?v=0.4.0-13' in html
    assert 'workspace-shell.js?v=0.4.0-13' in html
    assert ".shell-page-header h1:focus { outline: none; }" in shell_css
    for route in ("quote", "onboarding", "assurance"):
        assert f'id: "{route}"' in shell
    for legacy_route in (
        "overview", "materials", "oac", "data", "agents", "acceptance",
        "skills", "validation", "operations",
    ):
        assert f'{legacy_route}: "' in shell or f'"{legacy_route}": "' in shell
    assert "function routeFromHash()" in shell
    assert "function activate(route" in shell
    assert 'window.scrollTo({ top: 0, behavior: "auto" });' in shell
    assert 'window.addEventListener("hashchange"' in shell
    assert 'window.OrgRebaseWorkspaceShell = Object.freeze({' in shell
    assert 'state && state.enterprise_data_lineage && state.enterprise_data_lineage.source' in shell
    assert 'window.addEventListener("orgrebase:staterendered"' in shell
    assert 'window.addEventListener("orgrebase:oacadaptationchange"' in (CONSOLE / "app.js").read_text()
    assert '/api/workspace/state' not in shell
    prepare = shell.index('taskIntakeApi("/api/workspace/task-intake/prepare"')
    admit = shell.index('taskIntakeApi("/api/workspace/task-intake/admit"')
    run = shell.index('taskIntakeApi("/api/workspace/task-intake/run"')
    assert prepare < admit < run
    for element_id in ("task-intake-prepare", "task-intake-admit", "task-intake-run"):
        assert f'id="{element_id}"' in shell
    admit_body = shell.split("async function admitTaskIntake", 1)[1].split(
        "async function runTaskIntake", 1
    )[0]
    run_body = shell.split("async function runTaskIntake", 1)[1].split(
        "function bindTaskIntake", 1
    )[0]
    assert '/api/workspace/task-intake/admit' in admit_body
    assert '/api/workspace/task-intake/run' not in admit_body
    assert 'taskApproval = await taskIntakeApi' in admit_body
    assert '/api/workspace/task-intake/run' in run_body
    assert 'approval_receipt: taskApproval' in run_body
    assert 'prepare.addEventListener("click", prepareTaskIntake)' in shell
    assert 'admit.addEventListener("click", admitTaskIntake)' in shell
    assert 'run.addEventListener("click", runTaskIntake)' in shell
    assert "admitAndRunTaskIntake" not in shell
    assert 'method: "POST"' in shell
    assert 'headers["X-OrgRebase-Actor"] = actorId' in shell
    assert "actor_id: scope.actorId" in shell
    assert "candidate_receipt: taskCandidate" in shell
    assert "approval_receipt: taskApproval" in shell
    assert 'maxlength="500"' in shell
    assert "work_description: prompt.value" in shell
    assert 'window.OrgRebaseClient.json("/api/workspace/task-intake/work-description"' in shell
    assert 'method: "GET"' in shell
    assert 'headers["X-OrgRebase-Actor"] = actorId' in shell
    assert 'payload.semantic_use !== "TASK_INTENT_ONLY"' in shell
    assert "payload.contributes_business_facts !== false" in shell
    assert "payload.grants_authority !== false" in shell
    assert "payload.included_in_public_evidence !== false" in shell
    assert "payload.included_in_events_or_otlp !== false" in shell
    assert 'taskWorkDescriptionStatus = "not-retained"' in shell
    assert "此运行未保存工作说明原文。" in shell
    assert "The original work description was not retained for this run." in shell
    assert ".task-intake-private[hidden] { display: none !important; }" in shell_css
    assert 'taskCandidate.status !== "READY_FOR_CONFIRMATION"' in shell
    assert 'taskCandidate.status === "HOLD"' in shell
    assert 'window.refreshState === "function"' not in shell
    assert 'new CustomEvent("orgrebase:workspacechange"' in shell
    assert 'state.stage === "EMPTY"' in shell
    assert "node.hidden = awaitingFormation" in shell
    assert "legacyPrimaryAction.disabled = true" in shell
    assert '"/api/workspace/form"' not in shell
    assert re.search(r"taskCandidate\.prompt(?!_)", shell) is None
    assert "taskCandidate.prompt_digest" in shell
    assert "taskCandidate.prompt_length" in shell
    assert 'byId("task-intake-actor").removeAttribute("title")' in shell
    assert 'byId("task-intake-customer").removeAttribute("title")' in shell
    assert 'byId("task-intake-unknowns").removeAttribute("title")' in shell
    assert "Current employee work and raw private values never enter onboarding materials" in shell
    assert "原始私密值均不混入接入材料" in shell
    assert 'role: "tablist"' in shell
    assert 'data-shell-tab-route' in shell
    assert 'window.OrgRebaseClient.json("/api/workspace/run-progress"' in shell
    assert "followRunProgress();" in run_body
    assert 'STAGES.includes(result.stage)' in run_body
    assert '["WAITING", "RUNNING"].includes(progressStatus)' in shell
    assert "const RUN_PROGRESS_FOLLOW_WINDOW_MS = 15 * 60 * 1000" in shell
    assert "const RUN_PROGRESS_RETRY_DELAYS_MS = Object.freeze([1000, 2000, 4000])" in shell
    assert "runProgressFollowDeadline = Date.now() + RUN_PROGRESS_FOLLOW_WINDOW_MS" in shell
    assert 'id="observed-run-progress-resume"' in shell
    assert '.addEventListener("click", followRunProgress)' in shell
    assert 'progressFollowPaused: "前端观察已暂停；后端可能仍在运行，可刷新页面或恢复跟随。"' in shell
    assert 'progressRetryStopped: "进度连接仍不可用；后端可能仍在运行，可恢复跟随。"' in shell
    assert 'setRunProgressNotice("progressRetrying"' in shell
    assert 'setRunProgressNotice("progressRetryStopped", {}, { canResume: true })' in shell
    assert "RUN_PROGRESS_RETRY_DELAYS_MS[runProgressRetryCount - 1]" in shell
    assert "await refreshRunProgress({ follow: runProgressFollowing });" in run_body
    assert 'activateShellTab("quote", "collaboration", { focus: true });' in run_body
    assert '.shell-page[data-page="quote"] .command-bar { position: static; }' in shell_css
    state_listener = shell.split(
        'window.addEventListener("orgrebase:staterendered"', 1
    )[1].split('window.addEventListener("orgrebase:stateunavailable"', 1)[0]
    assert "refreshRunProgress({ follow: runProgressFollowing });" in state_listener
    assert 'const taskHero = document.querySelector("main > .hero")' in shell
    assert 'const taskComplexity = document.querySelector("main > .complexity-bridge")' in shell
    assert 'make("details", "task-context-details")' in shell
    assert "规则变化后，工作如何更新" in shell
    assert ".shell-page[hidden] { display: none !important; }" in shell_css
    assert ".shell-subpage[hidden] { display: none !important; }" in shell_css
    assert '.shell-subpage[data-shell-panel="changes"] .enterprise-data-journey-rail' in shell_css


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the progress-follow gate")
def test_workspace_progress_follow_retries_transient_failures_and_stops_truthfully() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    declarations = shell[
        shell.index("  let runProgressTimer = null;") : shell.index("  function renderRunProgress(progress)")
    ]
    refresh_runtime = shell[
        shell.index("  async function refreshRunProgress") : shell.index("  function navState(")
    ]
    node_script = declarations + "\n" + r'''
function renderRunProgress(progress) {
  return progress && progress.status || null;
}
''' + refresh_runtime + r'''
const assert = require("node:assert/strict");
let now = 1000;
const Date = { now: () => now };
const selected = {
  progressRetrying: "进度连接暂时中断，正在重试（{attempt}/{total}）…",
  progressFollowPaused: "前端观察已暂停；后端可能仍在运行，可刷新页面或恢复跟随。",
  progressRetryStopped: "进度连接仍不可用；后端可能仍在运行，可恢复跟随。",
};
const nodes = new Map([
  ["observed-run-progress-status", { textContent: "" }],
  ["observed-run-progress-resume", { hidden: true }],
]);
function copy() { return selected; }
function byId(id) { return nodes.get(id) || null; }
function format(template, values) {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
    template,
  );
}

const timers = [];
const scheduledDelays = [];
let timerId = 0;
const window = {
  OrgRebaseClient: { json: null },
  setTimeout(callback, delay) {
    timerId += 1;
    timers.push({ id: timerId, callback, delay });
    scheduledDelays.push(delay);
    return timerId;
  },
  clearTimeout(id) {
    const index = timers.findIndex((timer) => timer.id === id);
    if (index >= 0) timers.splice(index, 1);
  },
};
const runningResponse = { status: "RUNNING" };

(async () => {
  const transientQueue = [new Error("temporary network error"), runningResponse];
  window.OrgRebaseClient.json = async () => {
    const next = transientQueue.shift();
    if (next instanceof Error) throw next;
    return next;
  };
  runProgressFollowing = true;
  runProgressFollowDeadline = now + 10000;
  await refreshRunProgress({ follow: true });
  assert.equal(runProgressFollowing, true);
  assert.equal(runProgressRetryCount, 1);
  assert.equal(timers[0].delay, 1000);
  assert.equal(nodes.get("observed-run-progress-status").textContent, selected.progressRetrying
    .replace("{attempt}", "1").replace("{total}", "3"));
  assert.equal(nodes.get("observed-run-progress-resume").hidden, true);
  await timers.shift().callback();
  assert.equal(runProgressRetryCount, 0);
  assert.equal(timers[0].delay, 700);

  clearRunProgressTimer();
  scheduledDelays.length = 0;
  window.OrgRebaseClient.json = async () => { throw new Error("temporary network error"); };
  runProgressFollowing = true;
  runProgressRetryCount = 0;
  runProgressFollowDeadline = now + 10000;
  await refreshRunProgress({ follow: true });
  await timers.shift().callback();
  await timers.shift().callback();
  await timers.shift().callback();
  assert.deepEqual(scheduledDelays, [1000, 2000, 4000]);
  assert.equal(runProgressFollowing, false);
  assert.equal(nodes.get("observed-run-progress-status").textContent, selected.progressRetryStopped);
  assert.equal(nodes.get("observed-run-progress-resume").hidden, false);

  clearRunProgressTimer();
  scheduledDelays.length = 0;
  window.OrgRebaseClient.json = async () => { throw Object.assign(new Error("context changed"), { code: "WORKSPACE_REQUEST_CONTEXT_CHANGED" }); };
  runProgressFollowing = true;
  runProgressFollowDeadline = now + 10000;
  await refreshRunProgress({ follow: true });
  assert.equal(runProgressFollowing, false);
  assert.equal(timers.length, 0, "old identity progress must not schedule a new poll");

  let deadlineFetches = 0;
  window.OrgRebaseClient.json = async () => { deadlineFetches += 1; return runningResponse; };
  runProgressFollowing = true;
  runProgressFollowDeadline = now;
  await refreshRunProgress({ follow: true });
  assert.equal(deadlineFetches, 0);
  assert.equal(runProgressFollowing, false);
  assert.equal(nodes.get("observed-run-progress-status").textContent, selected.progressFollowPaused);
  assert.equal(nodes.get("observed-run-progress-resume").hidden, false);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_workspace_shell_keeps_onboarding_and_task_start_copy_truthful() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    assert 'data-route-target="oac" data-shell-copy="openOac"' in shell
    assert 'openOac: "继续组织契约适配"' in shell
    assert 'openOac: "Continue to organization-contract adaptation"' in shell
    assert "server data_class" not in shell
    assert "raw_private_value" not in shell

    assert 'runAwaitingEmployee: "等待员工发起工作"' in shell
    assert 'runAwaitingEmployee: "Business run starts after employee request"' in shell
    assert "运行编号已预留" not in shell
    assert "Execution ID reserved" not in shell

    assert 'taskAdmittedProof: "员工确认已绑定 · 等待启动智能体团队"' in shell
    assert 'taskAdmittedProof: "Employee confirmation bound · waiting to start the Agent team"' in shell
    assert 'taskAdmitted: "员工已确认 · 等待显式启动"' in shell
    assert 'taskAdmitted: "Employee confirmed · waiting for an explicit start"' in shell
    assert 'taskHoldProof: "工作意图校验未通过 · 未启动智能体团队"' in shell
    assert 'taskHoldProof: "Work-intent validation did not pass · Agent team not started"' in shell
    assert "          : candidateHold\n            ? selected.taskHoldProof" in shell
    assert (
        ": taskApproval\n"
        "          ? (taskIntakeError && !taskIntakeBusy ? selected.taskAdmittedRetryProof : selected.taskAdmittedProof)"
    ) in shell


def test_workspace_shell_starts_with_enterprise_onboarding_then_change_response() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    route_catalog = shell.split("const ROUTES = Object.freeze([", 1)[1].split("]);", 1)[0]

    assert route_catalog.count("{ id:") == 3
    assert 'id: "onboarding", icon: "◎", order: "01", section: "primary"' in route_catalog
    assert 'id: "quote", icon: "◆", order: "02", section: "primary"' in route_catalog
    assert 'id: "assurance", icon: "◇", order: "03", section: "secondary"' in route_catalog
    assert 'shell-nav-intro' not in shell
    assert 'sectionPrimary: "业务生命周期"' in shell
    assert 'sectionSecondary: "低频管理"' in shell
    assert 'let currentRoute = "onboarding";' in shell
    assert 'ROUTES.some((item) => item.id === route) ? route : "onboarding"' in shell
    assert "企业组织契约尚未激活，当前不能建立可演化基线" in shell
    assert "产品智能体 ↔ 产品负责人" in shell
    assert "智能体默认连续自动执行，只有精确权威边界才暂停并通知对应负责人" in shell
    assert "批准前规范写入为 0" in shell
    assert "不重复执行当前报价任务" in shell
    assert 'const acceptance = byId("current-task-acceptance");' in shell
    assert 'const validation = byId("cockpit-value")' in shell
    assert "function positionCurrentRunProof()" not in shell
    assert "ops-current-run-proof" not in shell
    assert '{ id: "collaboration", nodes: [collaboration] }' in shell
    assert 'if (route === "assurance") return "audit";' in shell
    assert 'audit: "可审计"' in shell
    assert 'renderContextFacts(currentState);' in shell


def test_change_driven_surface_separates_agent_execution_owner_pause_and_reference_baseline() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    for pair in (
        "产品智能体 ↔ 产品负责人",
        "法务智能体 ↔ 法务负责人",
        "财务智能体 ↔ 财务负责人",
        "市场与商业化智能体 ↔ 市场与商业化负责人",
    ):
        assert pair in shell
    assert "任务使用已激活组织契约中的事实与权限" in shell
    assert "智能体默认连续自动执行，只有精确权威边界才暂停并通知对应负责人" in shell
    assert "审批前看候选影响 · 审批后看后继版本" in html
    assert "参考人工基线" in html
    assert "Quote v1 has not been formed yet" in javascript
    assert "only later ChangeSets project a minimum change_team" in javascript
    assert "inspect each change's execution record to see whether native AT tasks actually ran" in javascript
    assert "they do not rebuild a native taskflow" not in javascript
    assert '"business.role.quoteOperator": "Quote Operations Owner"' in javascript
    assert "An Enterprise Quote Operator starts the task" not in javascript
    assert '"role.quoteOperator": "Quote Operations Owner"' in javascript
    assert '"value.primaryRole": "Quote Operations Owner"' in javascript
    assert "Enterprise Quote Change Owner" not in javascript
    assert '"business.role.quoteOperator": "报价运营负责人"' in javascript
    assert '"employee:enterprise-quote-operator": "报价运营负责人"' in Path(
        "demo/console/oac-adaptation.js"
    ).read_text(encoding="utf-8")
    assert "设计目标" not in html
    assert "设计目标" not in shell
    assert "设计目标" not in javascript

    stage_actions = javascript.split("const STAGE_ACTIONS = {", 1)[1].split("\n};", 1)[0]
    assert "preview:" in stage_actions
    assert "approve:" in stage_actions
    action_resolver = javascript.split("function actionForState", 1)[1].split(
        "function reviewIdentity", 1
    )[0]
    assert 'config.method === "approve" ? controls.owner_id' in action_resolver
    assert 'config.method === "preview" ? "system:orgrebase-impact-engine"' in action_resolver
    assert '["preview", "approve"].includes(action.method)' not in action_resolver

    runner = javascript.split("async function runPrimaryAction", 1)[1].split(
        "function bind", 1
    )[0]
    runner = runner.split("async function downloadJson", 1)[0]
    assert 'action.method === "open-change"' in runner
    assert "OrgRebaseChangeWorkbench.select(action.kind)" in runner
    assert "/api/workspace/approve/" not in runner
    assert "/api/workspace/apply/" not in runner


def test_quote_work_moves_one_pre_task_process_baseline_before_task_intake() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    # The legacy document owns exactly one copy of each reference node. The
    # shell creates one destination and moves those direct children into it.
    assert html.count('class="lane-note value-lane"') == 1
    assert html.count('class="acceptance-support-details"') == 1
    assert 'id="task-process-baseline"' not in html
    assert shell.count('id: "task-process-baseline"') == 1

    mover = shell.split("function buildTaskProcessBaseline", 1)[1].split(
        "function buildOverview", 1
    )[0]
    assert "const directChildren = Array.from(acceptance.children);" in mover
    assert 'node.classList.contains("lane-note") && node.classList.contains("value-lane")' in mover
    assert 'node.classList.contains("acceptance-support-details")' in mover
    assert "[valueLane, processDetails].filter(Boolean).forEach((node) => section.append(node));" in mover
    assert "clone" not in mover.lower()

    assembly = shell.split("function buildShell", 1)[1].split(
        "const quote = buildTabbedPage", 1
    )[0]
    expected_order = (
        'buildTaskPrerequisite(),',
        'buildTaskExecutionPreview(),',
        'buildTaskProcessBaseline(acceptance),',
        'buildTaskCompass(),',
        'buildTaskIntake(),',
    )
    positions = [assembly.index(marker) for marker in expected_order]
    assert positions == sorted(positions)
    assert assembly.index('const acceptance = byId("current-task-acceptance");') < positions[0]
    assert assembly.index("buildTaskProcessBaseline(acceptance)") < assembly.index(
        "if (acceptance) changes.append(acceptance);"
    )
    assert 'id="value-process-steps"' in html


def test_current_business_run_archives_only_after_same_run_terminal_closure() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "current-run-archive",
        "current-run-archive-status",
        "current-run-archive-id",
        "current-run-archive-quote",
        "current-run-archive-receipts",
        "current-run-archive-authority",
        "public-validation-run-id-summary",
    ):
        assert f'id="{element_id}"' in html
    assert 'api("/api/workspace/run-archive")' in javascript
    assert 'view.schema_version === "orgrebase.workspace-current-run-archive-view.v1"' in javascript
    assert 'record.archive_class === "CURRENT_BUSINESS_RUN_COMPLETION"' in javascript
    assert "record.same_run_as_current_task === true" in javascript
    assert "function currentRunArchiveProof(view, runId)" in javascript
    assert "view.failures.length === 0" in javascript
    assert 'view.claim_boundary === "CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING"' in javascript
    assert 'item.kind === "source_readmission_group" ? item.human_approval_count : 1' in javascript
    assert ") === record.human_approval_count" in javascript
    assert "isSha256Digest(receipt.approval_digest)" in javascript
    assert "isSha256Digest(receipt.rebase_receipt_digest)" in javascript
    assert "isSha256Digest(quote.digest)" in javascript
    assert "record.human_approval_count === 2" not in javascript
    assert "receipts.length === 2" in javascript
    assert 'receiptKinds[0] === "launch_date"' in javascript
    assert 'receiptKinds[1] === "currency"' in javascript
    assert 'receipts[0].successor_quote_version === "v2"' in javascript
    assert 'receipts[1].successor_quote_version === "v3"' in javascript
    assert 'record.canonical_authority === "ORGREBASE_CONTROL_PLANE"' in javascript
    assert 'root.dataset.state = "ARCHIVED"' in javascript
    assert "这不是第二次执行" in html
    assert "BPI 2019 公开真实采购到付款机制验证" in html


def test_validation_archives_visually_separate_this_run_from_frozen_independent_runs() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    current = html.index('id="current-run-archive"')
    frozen_boundary = html.index('id="frozen-validation-archive-boundary"')
    release = html.index('id="current-proof-overview"')
    public = html.index('id="public-real-process-validation"')
    assert current < frozen_boundary < release < public
    assert 'id="current-proof-overview" aria-labelledby="current-proof-title" open' not in html
    assert 'id="public-real-process-validation" aria-labelledby="public-validation-title" open' not in html
    assert 'id="retained-mechanism" aria-label=' in html
    assert 'data-i18n-aria-label="a11y.retainedEvidence" open' not in html
    assert "以下证据验证产品机制，不代表本次任务状态" in html
    assert "每张档案保留自己的 run_id" in html
    assert "retainedMechanism.open = true" not in shell
    assert "retainedMechanism.open = false" not in shell

    for element_id in (
        "public-validation-run-id",
        "public-adaptation-run-id-visible",
        "public-execution-run-id-visible",
        "public-validation-archive-time",
        "retained-archive-time",
    ):
        assert f'id="{element_id}"' in html
    public_renderer = javascript.split("function renderPublicRealProcessValidation", 1)[1].split(
        "\nfunction ", 1
    )[0]
    retained_renderer = javascript.split("function renderRetainedEvidence", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "validation.archive_identity_digest || verification.projection_digest" in public_renderer
    assert "validation.archive_snapshot_at" not in public_renderer
    assert "evidence.archive_identity_digest" in retained_renderer
    assert "evidence.archive_snapshot_at" not in retained_renderer
    assert "受控本地审批等待门" in html
    assert "候选适配与作用域执行是两条独立运行" in html


def test_quote_raci_separates_human_as_is_execution_from_post_change_execution() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    baseline = json.loads(
        (ROOT / "benchmark/quote-value-v0.1/public/current-process-baseline.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {
        "quote-step:intake": (("Quote Operator",), ("Quote Operator",)),
        "quote-step:product-confirmation": (("Product Owner",), ("Product Agent",)),
        "quote-step:legal-confirmation": (("Legal Owner",), ("Legal Agent",)),
        "quote-step:finance-confirmation": (("Finance Owner",), ("Finance Agent",)),
        "quote-step:composition": (("Quote Operator",), ("GTM Agent",)),
        "quote-step:delivery-acceptance": (("Quote Operator",), ("Quote Operator",)),
        "quote-step:change-impact": (("Changed-source Owner",), ("Deterministic Control",)),
        "quote-step:selective-update": (
            ("Exact Approval Owner",),
            ("Bounded Runtime", "Canonical Writer"),
        ),
    }
    assert {
        step["id"]: (tuple(step["responsible"]), tuple(step["to_be_responsible"]))
        for step in baseline["process_steps"]
    } == expected
    for key in (
        "value.table.asIsResponsible",
        "value.table.toBeResponsible",
        "value.table.accountable",
    ):
        assert f'data-i18n="{key}"' in html
    assert 'colspan="8"' in html
    renderer = javascript.split("function renderValueAndResponsibility", 1)[1].split(
        "\nfunction retainedWorkerBinding", 1
    )[0]
    assert "step.to_be_responsible" in renderer
    assert "TO_BE_RESPONSIBLE_BY_PROCESS_STEP[step.id]" in renderer
    assert 'title="${escapeHtml(processLabel)}"' in renderer


def test_current_run_observability_reuses_one_runtime_assurance_card_and_requests_once() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    assert html.count('id="ops-current-run-proof"') == 1
    assert (
        html.index('id="cockpit-operations"')
        < html.index('id="ops-current-run-proof"')
        < html.index('id="ops-receipts"')
    )
    assert "ops-current-run-proof" not in shell
    assert "positionCurrentRunProof" not in shell
    assert javascript.count('api("/api/workspace/run-observability")') == 1
    request_gate = javascript.split(
        "function refreshCompletedRunObservabilityProjection", 1
    )[1].split("\nfunction refreshCurrentRunArchive", 1)[0]
    assert "const runId = terminalRunId(state);" in request_gate
    assert "!currentRunArchiveProof(archiveView, runId)" in request_gate
    assert "currentRunObservabilityRequestedFor === completionKey" in request_gate
    assert request_gate.index("currentRunArchiveProof") < request_gate.index(
        'api("/api/workspace/run-observability")'
    )
    assert 'currentRunObservability = view && typeof view === "object"' in request_gate
    assert '{ status: "UNAVAILABLE", run_id: runId }' in request_gate
    helper = javascript.split(
        "function completedRunObservabilityProjection", 1
    )[1].split("\nfunction renderCurrentRunArchive", 1)[0]
    for forbidden in (
        "currentRetainedEvidence",
        "/api/platform/evidence",
        "operations.otlp",
        "signal_digests",
    ):
        assert forbidden not in helper
        assert forbidden not in request_gate
    lane = javascript.split("function operationProofLane", 1)[1].split(
        "\nfunction nativeAgentTeamsFormationReceipt", 1
    )[0]
    assert "item.detail ||" in lane


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the UI truth-projection gates")
def test_release_archive_projection_accepts_dynamic_counts_and_fails_closed_on_binding_drift() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    release_helper = javascript.split("function releaseFactsProofProjection", 1)[1].split(
        "\nfunction proofFailureCodes", 1
    )[0]
    public_helper = javascript.split("function publicValidationProofProjection", 1)[1].split(
        "\nfunction renderCurrentProofOverview", 1
    )[0]
    node_script = r'''
const assert = require("node:assert/strict");
function isSha256Digest(value) {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}
''' + "function releaseFactsProofProjection" + release_helper + "\nfunction publicValidationProofProjection" + public_helper + r'''
const digest = (token) => `sha256:${token.repeat(64)}`;
const goldenRun = "run:published:golden";
const ownerGates = ["launch", "currency", "legal"].map((kind, index) => ({
  change_kind: kind,
  owner_id: `human:${kind}`,
  run_id: goldenRun,
  review_wait_satisfied: true,
  approval_digest: digest(String(index + 1)),
  binding_digest: digest(String(index + 4)),
}));
const release = {
  schema_version: "orgrebase.release-facts.v9",
  current_semifinal_delivery: {
    status: "PASS",
    failures: [],
    claim_boundary: { golden_and_dynamic_formation_are_independent_runs: true },
    golden_quote_delivery: {
      status: "PASS",
      failures: [],
      run_id: goldenRun,
      quote: { status: "PASS", stage: "QUOTE_V3", version: "v3", digest: digest("a") },
      human_owner_gates: { status: "PASS", count: ownerGates.length, gates: ownerGates },
      same_run_agentteams_skill_tool: {
        status: "PASS",
        run_id: goldenRun,
        agentteams_summary_digest: digest("b"),
        tool_receipt_digest: digest("c"),
        skill_invocation_receipt_digest: digest("d"),
      },
      oac_bound_task_formation: {
        status: "PASS",
        failures: [],
        run_id: goldenRun,
        task_intake_status: "FORMATION_COMPLETED",
        activation_status: "CONSUMED_BY_QUOTE_FORMATION",
        activation_binding_digest: digest("e"),
        task_formation_decision_receipt_digest: digest("f"),
        context_envelope_digest: digest("1"),
        agentteams_execution_plan_digest: digest("2"),
        planned_domain_ids: ["finance", "gtm", "legal"],
        actual_agentteams_domain_ids: ["legal", "finance", "gtm"],
        topology_match: true,
        candidate_only: true,
        canonical_target_writes: 0,
      },
      agents_are_candidate_only: true,
      candidate_layer_canonical_target_writes: 0,
      production_ready: false,
    },
    dynamic_formation_validation: {
      status: "PASS",
      failures: [],
      run_id: "run:published:formation",
      evidence_paths: { verification: "evidence/formation/verification.json", receipt: "evidence/formation/receipt.json" },
      selected_domain_ids: ["finance", "gtm", "legal", "product"],
      domain_task_count: 4,
      reviewer_task_count: 2,
      task_binding_count: 6,
      agentteams_action_count: 9,
      topology_match: true,
      candidate_only: true,
      canonical_target_writes: 0,
      production_ready: false,
    },
  },
  product_path_blackbox: {
    status: "PASS",
    benchmark_version: "ProductPath-v0.3-task-intake-bound",
    evidence_class: "LOCAL_REAL_HTTP_BLACKBOX",
    evidence_path: "evidence/product-path.json",
    case_count: 7,
    cases_passed: 7,
    mutation_count: 4,
    mutations_killed: 4,
    integrity_attacks: 3,
    integrity_attacks_rejected: 3,
    gate_attacks: 1,
    gate_attacks_rejected: 1,
    uvicorn_processes_started: 2,
    real_process_restarts: 1,
    single_byte_tamper: "PASS",
    retained_evaluator_replay: "PASS",
    reconstructed_wheel_bytes: "PASS",
    current_release_qualified: true,
    retained_raw_observations: "PASS",
    source_bound_oracle: "PASS",
    benchmark_migration: "PASS",
    task_intake_same_run_id: true,
    task_intake_same_workspace_nonce: true,
    task_intake_natural_language_authority: false,
    source_canonical_target_writes: 0,
    source_runtime_projection: { matched: 6, total: 6 },
    external_iam: "NOT_RUN",
    real_enterprise_generalization: "NOT_CLAIMED",
  },
};
let projected = releaseFactsProofProjection(release);
assert.deepEqual(
  [projected.goldenPassed, projected.formationPassed, projected.productPathPassed],
  [true, true, true],
);
const changedSnapshot = structuredClone(release);
changedSnapshot.product_path_blackbox.case_count = 9;
changedSnapshot.product_path_blackbox.cases_passed = 9;
assert.equal(releaseFactsProofProjection(changedSnapshot).productPathPassed, true);
const historical = structuredClone(release);
historical.product_path_blackbox.reconstructed_wheel_bytes = "NOT_RUN";
historical.product_path_blackbox.current_release_qualified = false;
historical.product_path_blackbox.fact_role = "HISTORICAL_COMPATIBILITY_BASELINE";
assert.equal(releaseFactsProofProjection(historical).productPathPassed, false);
assert.equal(releaseFactsProofProjection(historical).productPathHistorical, true);
historical.product_path_blackbox.cases_passed -= 1;
assert.equal(releaseFactsProofProjection(historical).productPathHistorical, false);

for (const [field, mutate] of Object.entries({
  deliveryFailure: (copy) => { copy.current_semifinal_delivery.failures = ["BROKEN"]; },
  digestDrift: (copy) => { copy.current_semifinal_delivery.golden_quote_delivery.same_run_agentteams_skill_tool.tool_receipt_digest = "sha256:short"; },
  missingGateOwner: (copy) => { delete copy.current_semifinal_delivery.golden_quote_delivery.human_owner_gates.gates[0].owner_id; },
  duplicateGateKind: (copy) => { copy.current_semifinal_delivery.golden_quote_delivery.human_owner_gates.gates[1].change_kind = "launch"; },
  runDrift: (copy) => { copy.current_semifinal_delivery.dynamic_formation_validation.run_id = goldenRun; },
  formationCountDrift: (copy) => { copy.current_semifinal_delivery.dynamic_formation_validation.domain_task_count += 1; },
  formationTooSmall: (copy) => {
    copy.current_semifinal_delivery.dynamic_formation_validation.selected_domain_ids = ["finance"];
    copy.current_semifinal_delivery.dynamic_formation_validation.domain_task_count = 1;
    copy.current_semifinal_delivery.dynamic_formation_validation.task_binding_count = 3;
  },
  productCountDrift: (copy) => { copy.product_path_blackbox.cases_passed -= 1; },
  productFailure: (copy) => { copy.product_path_blackbox.failures = ["BROKEN"]; },
  unknownBenchmark: (copy) => { copy.product_path_blackbox.benchmark_version = "ProductPath-unknown"; },
  unknownEvidenceClass: (copy) => { copy.product_path_blackbox.evidence_class = "UNKNOWN"; },
  emptyMutationProbe: (copy) => { copy.product_path_blackbox.mutation_count = 0; copy.product_path_blackbox.mutations_killed = 0; },
  emptyIntegrityProbe: (copy) => { copy.product_path_blackbox.integrity_attacks = 0; copy.product_path_blackbox.integrity_attacks_rejected = 0; },
  emptyGateProbe: (copy) => { copy.product_path_blackbox.gate_attacks = 0; copy.product_path_blackbox.gate_attacks_rejected = 0; },
  noRestart: (copy) => { copy.product_path_blackbox.real_process_restarts = 0; },
})) {
  const copy = structuredClone(release);
  mutate(copy);
  projected = releaseFactsProofProjection(copy);
  if (field === "deliveryFailure") assert.deepEqual([projected.goldenPassed, projected.formationPassed], [false, false]);
  if (["digestDrift", "missingGateOwner", "duplicateGateKind"].includes(field)) assert.equal(projected.goldenPassed, false);
  if (["runDrift", "formationCountDrift", "formationTooSmall"].includes(field)) assert.equal(projected.formationPassed, false);
  if ([
    "productCountDrift",
    "productFailure",
    "unknownBenchmark",
    "unknownEvidenceClass",
    "emptyMutationProbe",
    "emptyIntegrityProbe",
    "emptyGateProbe",
    "noRestart",
  ].includes(field)) assert.equal(projected.productPathPassed, false);
}

const publicValidation = {
  schema_version: "orgrebase.public-real-process-validation-view.v1",
  status: "PASS",
  generated: true,
  read_model_target_writes: 0,
  archive_status: "FROZEN_HISTORICAL_VALIDATION",
  current_task_run: false,
  run_id: "run:public:bpi",
  claim_boundary: "NOT_REAL_QUOTE_DATA",
  source: { raw_sha256: digest("3") },
  verification: {
    status: "PASS",
    benchmark_receipt_digest: digest("4"),
    replay_receipt_digest: digest("5"),
    projection_digest: digest("6"),
    summary_digest: digest("7"),
  },
  agentic_adaptation: {
    status: "PASS",
    adaptation_run_id: "run:public:adaptation",
    execution_run_id: "run:public:execution",
    model: { status: "VALID", provider_request_observed: true },
    agentteams: { terminal_state: "completed", action_count: 3 },
    mapping: { deterministic_verdict: "PASS", unknown_count: 2 },
    execution: { canonical_target_writes: 0 },
    verification: { closed_world_manifest_digest: digest("8"), adaptation_receipt_digest: digest("9") },
    limitations: ["NOT_ARBITRARY_ENTERPRISE_ADAPTATION"],
    read_model_target_writes: 0,
  },
};
assert.deepEqual(publicValidationProofProjection(publicValidation), {
  passed: true,
  agentic: publicValidation.agentic_adaptation,
  agenticPassed: true,
  source: publicValidation.source,
  verification: publicValidation.verification,
});
const publicDigestDrift = structuredClone(publicValidation);
publicDigestDrift.verification.projection_digest = "sha256:short";
assert.equal(publicValidationProofProjection(publicDigestDrift).passed, false);
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the current-run archive gate")
def test_current_run_archive_projection_uses_payload_counts_and_exact_receipt_bindings() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    helper = javascript.split("const verifiedApprovalAuthorities", 1)[1].split(
        "\nfunction renderCurrentRunArchive", 1
    )[0]
    node_script = r'''
const assert = require("node:assert/strict");
function isSha256Digest(value) {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}
''' + "const verifiedApprovalAuthorities" + helper + r'''
const digest = (token) => `sha256:${token.repeat(64)}`;
const runId = "run:current:quote";
const receipts = ["launch_date", "currency"].map((kind, index) => ({
  kind,
  owner_id: `human:${kind}`,
  preview_digest: digest(String(index + 1)),
  approval_digest: digest(String(index + 4)),
  rebase_receipt_digest: digest(String(index + 7)),
  workspace_receipt_digest: digest(String.fromCharCode(97 + index)),
  successor_quote_version: `v${index + 2}`,
}));
const view = {
  schema_version: "orgrebase.workspace-current-run-archive-view.v2",
  status: "ARCHIVED",
  run_id: runId,
  stage: "CURRENT",
  business_complete: true,
  failures: [],
  claim_boundary: "CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING",
  record: {
    archive_class: "CURRENT_BUSINESS_RUN_COMPLETION",
    same_run_as_current_task: true,
    terminal_status: "COMPLETED",
    run_id: runId,
    human_approval_count: receipts.length,
    selective_rebase_receipts: receipts,
    quote: { ref: "work:quote@v3", digest: digest("f") },
    quote_event_count: 8,
    canonical_authority: "ORGREBASE_CONTROL_PLANE",
    evidence_class: "VERIFIED_SAME_RUN_CANONICAL_STATE",
  },
};
assert.equal(currentRunArchiveProof(view, runId), true);
for (const mutate of [
  (copy) => { copy.failures = ["BROKEN"]; },
  (copy) => { copy.record.evidence_class = "VERIFIED_SAME_RUN_CONTROLLED_LOCAL"; },
  (copy) => { copy.schema_version = "orgrebase.workspace-current-run-archive-view.v99"; },
  (copy) => { copy.run_id = "run:other"; },
  (copy) => { copy.record.human_approval_count += 1; },
  (copy) => {
    copy.record.selective_rebase_receipts.push({
      ...copy.record.selective_rebase_receipts[1],
      kind: "legal",
    });
    copy.record.human_approval_count += 1;
  },
  (copy) => { copy.record.selective_rebase_receipts[0].kind = ""; },
  (copy) => { copy.record.selective_rebase_receipts[0].successor_quote_version = "v9"; },
  (copy) => { copy.record.selective_rebase_receipts[1].successor_quote_version = "v2"; },
  (copy) => { copy.record.selective_rebase_receipts[0].approval_digest = "sha256:short"; },
  (copy) => { copy.record.quote.digest = "sha256:short"; },
]) {
  const copy = structuredClone(view);
  mutate(copy);
  assert.equal(currentRunArchiveProof(copy, runId), false);
}
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js is required for the completed-run observability gate",
)
def test_real_completed_run_projection_passes_node_gate_and_tampering_fails_closed() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    digest_helper = "function isSha256Digest" + javascript.split(
        "function isSha256Digest", 1
    )[1].split("\n\nfunction escapeHtml", 1)[0]
    archive_helper = "const verifiedApprovalAuthorities" + javascript.split(
        "const verifiedApprovalAuthorities", 1
    )[1].split("\nfunction completedRunObservabilityProjection", 1)[0]
    projection_helper = "function completedRunObservabilityProjection" + javascript.split(
        "function completedRunObservabilityProjection", 1
    )[1].split("\nfunction renderCurrentRunArchive", 1)[0]

    state = json.loads(
        (ROOT / "evidence/golden-competition/latest/pilot/state.json").read_text(
            encoding="utf-8"
        )
    )

    class StateSnapshot:
        def state(self) -> dict[str, object]:
            return state

    archive = _workspace_current_run_archive_view(StateSnapshot())
    projection = build_completed_run_observability_from_trusted_state(state, archive)
    assert projection["status"] == "PROJECTED", projection["failures"]

    node_script = digest_helper + "\n" + archive_helper + "\n" + projection_helper + r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const runId = payload.projection.run_id;
const result = completedRunObservabilityProjection(payload.projection, payload.archive, runId);
assert.equal(result.status, "PASS");
assert.deepEqual(result.items.map((item) => item.stage), [
  "SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "APPROVAL", "APPLY", "TERMINAL",
]);
assert.deepEqual(result.items.map((item) => item.target_writes), [0, 0, 0, 0, 0, 0, 0]);

const spans = (view) => view.otlp.traces.resourceSpans[0].scopeSpans[0].spans;
const logs = (view) => view.otlp.logs.resourceLogs[0].scopeLogs[0].logRecords;
const point = (view) => view.otlp.metrics.resourceMetrics[0].scopeMetrics[0]
  .metrics[0].gauge.dataPoints[0];
const correlatedAttributes = (view) => [
  ...spans(view).map((span) => span.attributes),
  ...logs(view).map((record) => record.attributes),
  point(view).attributes,
];
const resources = (view) => [
  view.otlp.traces.resourceSpans[0].resource,
  view.otlp.logs.resourceLogs[0].resource,
  view.otlp.metrics.resourceMetrics[0].resource,
];
const setAttribute = (attributes, key, valueKey, value) => {
  const item = attributes.find((candidate) => candidate.key === key);
  item.value = { [valueKey]: value };
};
const deleteAttribute = (attributes, key) => {
  const index = attributes.findIndex((candidate) => candidate.key === key);
  if (index >= 0) attributes.splice(index, 1);
};
const attacks = [
  ["foreign run", (view) => { view.run_id = "run:foreign"; }],
  ["missing organization binding and attributes", (view) => {
    delete view.completion_binding.organization_id;
    for (const attributes of correlatedAttributes(view)) {
      deleteAttribute(attributes, "orgrebase.organization.id");
    }
  }],
  ["missing AgentTeams project binding and attributes", (view) => {
    delete view.completion_binding.agentteams_project_id;
    for (const attributes of correlatedAttributes(view)) {
      deleteAttribute(attributes, "orgrebase.agentteams.task.id");
      deleteAttribute(attributes, "orgrebase.agentteams.project.id");
    }
  }],
  ["synchronized unrelated Skill", (view) => {
    view.completion_binding.source_skill_ref = "skill:unrelated@9.9.9";
    view.completion_binding.skill_package_id = "skill-package:unrelated@9.9.9";
    for (const attributes of correlatedAttributes(view)) {
      setAttribute(attributes, "orgrebase.skill.name", "stringValue", "unrelated");
      setAttribute(attributes, "orgrebase.skill.version", "stringValue", "9.9.9");
    }
  }],
  ["empty service version", (view) => {
    for (const resource of resources(view)) {
      setAttribute(resource.attributes, "service.version", "stringValue", "");
    }
  }],
  ["quote drift", (_view, archived) => { archived.record.quote.digest = `sha256:${"0".repeat(64)}`; }],
  ["swapped approvals", (view) => { view.completion_binding.approval_apply_summaries.reverse(); }],
  ["reordered layers", (view) => {
    [view.projection_receipt.layer_order[0], view.projection_receipt.layer_order[1]] =
      [view.projection_receipt.layer_order[1], view.projection_receipt.layer_order[0]];
  }],
  ["missing trace", (view) => { spans(view).pop(); }],
  ["bad parent", (view) => { spans(view)[1].parentSpanId = "f".repeat(16); }],
  ["missing log", (view) => { logs(view).pop(); }],
  ["metric count six", (view) => { point(view).asInt = "6"; }],
  ["write one", (view) => {
    setAttribute(spans(view)[0].attributes, "orgrebase.target.write.count", "intValue", "1");
  }],
  ["production flag", (view) => { view.projection_receipt.production_backend_claimed = true; }],
  ["receipt binding drift", (view) => {
    view.projection_receipt.completion_binding_digest = `sha256:${"1".repeat(64)}`;
  }],
  ["frozen OTLP substitution", (view) => {
    const frozenRun = "run:frozen:independent";
    for (const span of spans(view)) {
      setAttribute(span.attributes, "orgrebase.workflow.run_id", "stringValue", frozenRun);
    }
    for (const record of logs(view)) {
      setAttribute(record.attributes, "orgrebase.workflow.run_id", "stringValue", frozenRun);
    }
    setAttribute(point(view).attributes, "orgrebase.workflow.run_id", "stringValue", frozenRun);
  }],
];
for (const [name, mutate] of attacks) {
  const view = structuredClone(payload.projection);
  const archived = structuredClone(payload.archive);
  mutate(view, archived);
  assert.equal(
    completedRunObservabilityProjection(view, archived, runId),
    null,
    name,
  );
}
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        input=json.dumps({"projection": projection, "archive": archive}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_capability_governance_is_separate_from_current_agent_run_and_fail_closed() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    for element_id in (
        "cockpit-skills",
        "skill-current-invocation",
        "skill-current-agent",
        "skill-current-name",
        "published-skills-mount",
        "experience-governance-mount",
    ):
        assert f'id="{element_id}"' in html
    assert "function positionPlatformGovernance()" in shell
    assert "publishedMount.append(publishedSkills)" in shell
    assert "experienceMount.append(experience)" in shell
    assert "validationView.insertBefore(publicValidation, currentProof)" in shell
    assert "validationView.append(retainedMechanism)" in shell
    assert "function exactGtmSkillInvocation(rows, runId, skill, task)" in javascript
    assert "sameRunActivityRow(row, runId)" in javascript
    assert 'domain: "gtm"' in javascript
    assert 'action: "COMPOSE_REVIEWED_QUOTE_CANDIDATE"' in javascript
    assert 'row.permission === "CANDIDATE_ONLY"' in javascript
    assert 'row.receipt_digest === expected.receiptDigest' in javascript
    assert 'row.tool_or_skill === expected.toolOrSkill' in javascript
    assert 'task.agent_name === row.actor_or_domain' in javascript
    assert "智能体使用受版本管理的能力" in html
    assert "人工决策是 Skill 发布批准，不是业务结果批准" in html
    assert 'class="skill-technical-details"' in javascript
    assert "查看技术回执" in javascript


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for run-binding truth gates")
def test_current_run_and_capability_bindings_fail_closed_behaviorally() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    helper_start = javascript.index("function currentRunId")
    helper_end = javascript.index("\n\nfunction activePilotActions", helper_start)
    helpers = javascript[helper_start:helper_end]
    node_script = helpers + r'''
const assert = require("node:assert/strict");

const runId = "run:current";
const baseExecution = {
  run_id: runId,
  mode: "LOCAL_DETERMINISTIC",
  candidate_runtime: "BASE_RUNTIME",
  canonical_authority: "StateStore",
  agentteams: "NOT_RUN",
};
const matchingState = {
  execution: baseExecution,
  competition_evidence: { status: "PASS", run_id: runId, correlation_id: "corr:1" },
};
assert.equal(activeCompetition(matchingState).run_id, runId);
assert.equal(stateExecution(matchingState).run_id, runId);
assert.equal(stateExecution(matchingState).mode, "CONTROLLED_LOCAL_AGENTTEAMS");

const staleState = {
  execution: baseExecution,
  competition_evidence: { status: "PASS", run_id: "run:stale", correlation_id: "corr:stale" },
};
assert.equal(activeCompetition(staleState), null);
assert.equal(stateExecution(staleState).run_id, runId);
assert.equal(stateExecution(staleState).mode, "LOCAL_DETERMINISTIC");
assert.equal(activeCompetition({ competition_evidence: { status: "PASS", run_id: runId } }), null);

const financeTask = {
  agent_name: "finance-steward",
  role: "DOMAIN_WORKER",
  authority_domain: "finance",
  attempt: 2,
  candidate_only: true,
  target_writes: 0,
};
const tool = {
  operation: "READ_DEPENDENCY_EVIDENCE",
  status: "SUCCEEDED",
  target_writes: 0,
  receipt_digest: "sha256:tool",
};
const toolRow = {
  run_id: runId,
  source_run_id: runId,
  plane: "TOOL",
  actor_or_domain: "finance-steward",
  action: "RECOVER_FINANCE_DEPENDENCY",
  tool_or_skill: "READ_DEPENDENCY_EVIDENCE",
  source_ref: "READ_DEPENDENCY_EVIDENCE",
  status: "SUCCEEDED",
  permission: "CANDIDATE_ONLY",
  target_writes: 0,
  receipt_digest: "sha256:tool",
};
assert.equal(exactFinanceToolInvocation([toolRow], runId, tool, financeTask), toolRow);
for (const mutation of [
  { source_run_id: "run:stale" },
  { actor_or_domain: "gtm-steward" },
  { permission: "READ_ONLY" },
  { target_writes: 1 },
  { receipt_digest: "sha256:wrong" },
  { tool_or_skill: "OTHER_TOOL" },
]) {
  assert.equal(exactFinanceToolInvocation([{ ...toolRow, ...mutation }], runId, tool, financeTask), null);
}
assert.equal(exactFinanceToolInvocation([toolRow], runId, tool, { ...financeTask, attempt: 1 }), null);
assert.equal(exactFinanceToolInvocation([toolRow], runId, tool, { ...financeTask, authority_domain: "legal" }), null);

const gtmTask = {
  agent_name: "gtm-steward",
  role: "DOMAIN_WORKER",
  authority_domain: "gtm",
  attempt: 1,
  candidate_only: true,
  target_writes: 0,
};
const skill = {
  package_id: "skill-package:enterprise-quote-compose@1.3.0",
  status: "SUCCESS",
  target_writes: 0,
  receipt_digest: "sha256:skill",
};
const skillRow = {
  run_id: runId,
  source_run_id: runId,
  plane: "SKILL",
  actor_or_domain: "gtm-steward",
  action: "COMPOSE_REVIEWED_QUOTE_CANDIDATE",
  tool_or_skill: skill.package_id,
  source_ref: skill.package_id,
  status: "SUCCESS",
  permission: "CANDIDATE_ONLY",
  target_writes: 0,
  receipt_digest: "sha256:skill",
};
assert.equal(exactGtmSkillInvocation([skillRow], runId, skill, gtmTask), skillRow);
for (const mutation of [
  { run_id: "run:stale" },
  { source_run_id: "run:stale" },
  { actor_or_domain: "finance-steward" },
  { permission: "CANONICAL_WRITE" },
  { target_writes: 1 },
  { receipt_digest: "sha256:wrong" },
  { tool_or_skill: "skill-package:wrong@1.0.0" },
  { source_ref: "skill-package:wrong@1.0.0" },
]) {
  assert.equal(exactGtmSkillInvocation([{ ...skillRow, ...mutation }], runId, skill, gtmTask), null);
}
assert.equal(exactGtmSkillInvocation([skillRow], runId, skill, { ...gtmTask, role: "REVIEWER" }), null);
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the Task Intake UI gate")
def test_workspace_shell_only_restores_employee_confirmation_from_exact_persisted_intake() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    projection_source = "const DIGEST_PATTERN" + shell.split("const DIGEST_PATTERN", 1)[1].split(
        "function renderTaskIntake", 1
    )[0]
    digest = lambda character: f"sha256:{character * 64}"  # noqa: E731
    receipt = {
        "schema_version": "orgrebase.workspace-task-intake-run-receipt.v1",
        "status": "FORMATION_COMPLETED",
        "run_id": "run:quote-1",
        "actor_id": "employee:enterprise-quote-operator",
        "prompt_digest": digest("a"),
        "prompt_length": 42,
        "candidate_digest": digest("b"),
        "approval_digest": digest("c"),
        "task_digest": digest("d"),
        "formation_receipt_digest": digest("e"),
        "quote_ref": "quote:blue-harbor:v1",
        "oac_activation_binding_digest": digest("f"),
        "intake_persisted": True,
        "intake_canonical_target_writes": 0,
        "formation_authority": "ORGREBASE_CONTROL_PLANE",
        "claim_boundary": "INTAKE_GATE_VERIFIED_THEN_EXISTING_CONTROL_PLANE_FORMED_QUOTE",
        "digest": digest("1"),
        "artifact_id": "workspace/task-intake/run-receipt.json",
        "artifact_payload_digest": digest("2"),
        "event_digest": digest("3"),
    }
    node_script = f"""
{projection_source}
const assert = require("node:assert/strict");
const receipt = {json.dumps(receipt)};
const state = {{ stage: "QUOTE_V1", execution: {{ run_id: "run:quote-1" }}, task_intake: receipt }};
assert.equal(persistedTaskIntake(state), receipt);
assert.equal(persistedTaskIntake({{ ...state, task_intake: null }}), null);
assert.equal(persistedTaskIntake({{ ...state, execution: {{ run_id: "run:other" }} }}), null);
assert.equal(persistedTaskIntake({{ ...state, task_intake: {{ ...receipt, event_digest: null }} }}), null);
assert.equal(persistedTaskIntake({{ ...state, task_intake: {{ ...receipt, intake_persisted: false }} }}), null);
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    formed_projection = shell.split("function renderTaskIntake", 1)[1].split(
        "async function prepareTaskIntake", 1
    )[0]
    assert "persistedTaskIntake(state)" in formed_projection
    assert "selected.taskEvidenceUnavailable" in formed_projection
    assert "selected.taskEvidenceUnavailableDetail" in formed_projection
    assert "taskRunSummary" not in formed_projection
    assert "durableReceipt.prompt_digest" in formed_projection
    assert "durableReceipt.candidate_digest" in formed_projection
    assert "durableReceipt.approval_digest" in formed_projection
    assert "durableReceipt.formation_receipt_digest" in formed_projection


def test_console_never_turns_loading_or_fetch_failure_into_empty_workspace() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    render_source = javascript.split("function render(state)", 1)[1].split(
        "function renderSemifinalEvidence", 1
    )[0]
    assert "renderWorkspaceUnavailable();" in render_source
    assert "const normalized = state;" in render_source
    assert 'stage: "EMPTY"' not in render_source
    assert 'render({ stage: "EMPTY"' not in javascript
    assert 'workspaceStateAvailability = "unavailable"' in javascript
    assert 'new CustomEvent("orgrebase:stateunavailable"' in javascript
    assert "页面不会把失败伪装成空工作区" in javascript
    assert 'let stateAvailability = "loading";' in shell
    assert 'stateAvailability === "ready"' in shell
    assert 'renderState(null, { availability: "unavailable" })' in shell


def test_workspace_shell_preserves_original_orgrebase_visual_language() -> None:
    shell_css = (CONSOLE / "workspace-shell.css").read_text(encoding="utf-8")

    for token in (
        "var(--canvas)",
        "var(--paper)",
        "var(--ink)",
        "var(--hair-dark)",
        "var(--orange)",
        "var(--orange-soft)",
        "var(--display)",
        "var(--mono)",
    ):
        assert token in shell_css
    assert "linear-gradient" not in shell_css
    assert "radial-gradient" not in shell_css
    assert "border-radius: 12px" not in shell_css
    assert "box-shadow" not in shell_css


def test_console_has_no_remote_runtime_dependency_and_all_js_ids_exist() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "https://" not in html
    declared_ids = set(re.findall(r'\bid="([^"]+)"', html))
    referenced_ids = set(re.findall(r'byId\("([^"]+)"\)', javascript))
    assert referenced_ids <= declared_ids


def test_current_task_results_never_fall_back_to_historical_or_modelled_evidence() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    for premature_value in ("49.4%", "8.00", "4.05"):
        assert premature_value not in html
        assert premature_value not in javascript
    assert 'if (!state || !state.business_complete) return { status: "WAITING", stage };' in javascript
    assert "projection.receipt.workflow_run_id === execution.run_id" in javascript
    assert 'quoteEventScope.status === "PASS" && Number(quoteEventScope.events) > 0' in javascript
    assert "requiredMetricKeys.every" in javascript
    assert 'projection.workspaceReceipt.status === "COMPLETED"' in javascript
    assert "function isObservedCount" in javascript
    assert "Number.isSafeInteger(value) && value >= 0" in javascript
    assert "execution.run_id === declaredExecutionRunId" in javascript
    assert "competition.run_id === declaredExecutionRunId" in javascript
    assert "renderCurrentRunValue(state, currentRetainedEvidence);" in javascript
    assert 'class="retained-mechanism-archive"' in html
    assert "不代表当前任务已执行" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the current-run value truth gate")
def test_current_run_value_gate_is_behavioral_and_fails_closed() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    helper_start = javascript.index("function isObservedCount")
    helper_end = javascript.index("\n\nfunction currentRunValueCard", helper_start)
    truth_gate = javascript[helper_start:helper_end]
    node_script = truth_gate + r'''
const assert = require("node:assert/strict");
function activeCompetition(state) {
  const evidence = state && state.competition_evidence;
  return evidence && evidence.status === "PASS" ? evidence : null;
}
function stateExecution(state) {
  const competition = activeCompetition(state);
  return competition ? { ...state.execution, run_id: competition.run_id } : state.execution;
}
function changeProjection(_kind, value) { return value; }

const runId = "run:golden:test";
const projection = (quoteVersion, quoteDigest) => ({
  receiptComplete: true,
  receipt: {
    digest: `sha256:receipt-${quoteVersion}`,
    workflow_run_id: runId,
    metrics: {
      work_items_rebased: 1,
      bounded_unaffected: 2,
      unknown: 1,
      skills_requalified: 0,
      false_invalidations: 0,
      unauthorized_disclosures: 0,
    },
  },
  workspaceReceipt: { status: "COMPLETED", digest: `sha256:workspace-${quoteVersion}` },
  outcomeQuote: { version: quoteVersion, digest: quoteDigest },
  metrics: { rebuilt: 1, preserved: 2, unknown: 1, falseInvalidations: 0, unauthorized: 0 },
});
const terminal = {
  stage: "CURRENT",
  business_complete: true,
  execution: { run_id: runId },
  competition_evidence: { status: "PASS", run_id: runId },
  quote: { version: "v3", digest: "sha256:quote-v3" },
  event_scopes: { quote_business: { status: "PASS", events: 1 } },
  value_and_responsibility: { cost_model: { full: 8.00, selective: 4.05, saving: "49.4%" } },
  public_process_validation: { status: "PASS", saving: "99.6980%" },
  changes: {
    launch_date: projection("v2", "sha256:quote-v2"),
    currency: projection("v3", "sha256:quote-v3"),
  },
};

for (const stage of ["EMPTY", "CURRENT", "PREVIEWED", "APPROVED"]) {
  const state = structuredClone(terminal);
  state.stage = stage;
  state.business_complete = false;
  assert.equal(currentRunValueProjection(state).status, "WAITING");
}
const observed = currentRunValueProjection(terminal);
assert.equal(observed.status, "PASS");
assert.deepEqual(
  { decisions: observed.decisions, rebuilt: observed.rebuilt, preserved: observed.preserved, unknown: observed.unknown },
  { decisions: 8, rebuilt: 2, preserved: 4, unknown: 2 },
);
assert.equal(Object.values(observed).includes("49.4%"), false);

for (const invalidMetric of [null, "", "2", true, -1, 1.5]) {
  const invalidState = structuredClone(terminal);
  invalidState.changes.currency.receipt.metrics.work_items_rebased = invalidMetric;
  assert.equal(currentRunValueProjection(invalidState).status, "INVALID");
}
const missingMetric = structuredClone(terminal);
delete missingMetric.changes.currency.receipt.metrics.work_items_rebased;
assert.equal(currentRunValueProjection(missingMetric).status, "INVALID");
const wrongReceiptRun = structuredClone(terminal);
wrongReceiptRun.changes.currency.receipt.workflow_run_id = "run:other";
assert.equal(currentRunValueProjection(wrongReceiptRun).status, "INVALID");
const wrongCompetitionRun = structuredClone(terminal);
wrongCompetitionRun.competition_evidence.run_id = "run:other";
assert.equal(currentRunValueProjection(wrongCompetitionRun).status, "INVALID");
const zeroEvents = structuredClone(terminal);
zeroEvents.event_scopes.quote_business.events = 0;
assert.equal(currentRunValueProjection(zeroEvents).status, "INVALID");
const missingQuoteEvents = structuredClone(terminal);
delete missingQuoteEvents.event_scopes.quote_business;
assert.equal(currentRunValueProjection(missingQuoteEvents).status, "INVALID");
const failedQuoteEvents = structuredClone(terminal);
failedQuoteEvents.event_scopes.quote_business.status = "FAIL";
assert.equal(currentRunValueProjection(failedQuoteEvents).status, "INVALID");
const missingWorkspaceReceipt = structuredClone(terminal);
delete missingWorkspaceReceipt.changes.currency.workspaceReceipt;
assert.equal(currentRunValueProjection(missingWorkspaceReceipt).status, "INVALID");
const incompleteWorkspaceReceipt = structuredClone(terminal);
incompleteWorkspaceReceipt.changes.currency.workspaceReceipt.status = "WAITING";
assert.equal(currentRunValueProjection(incompleteWorkspaceReceipt).status, "INVALID");
const digestlessWorkspaceReceipt = structuredClone(terminal);
delete digestlessWorkspaceReceipt.changes.currency.workspaceReceipt.digest;
assert.equal(currentRunValueProjection(digestlessWorkspaceReceipt).status, "INVALID");
const wrongFinalQuote = structuredClone(terminal);
wrongFinalQuote.quote.digest = "sha256:wrong";
assert.equal(currentRunValueProjection(wrongFinalQuote).status, "INVALID");
const spoofedQuoteVersion = structuredClone(terminal);
spoofedQuoteVersion.quote.version = "v2";

assert.equal(currentRunValueProjection(spoofedQuoteVersion).status, "INVALID");
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the modelled-value receipt gate")
def test_modelled_value_appears_only_from_a_complete_verified_retained_receipt() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    helper = javascript.split("function validatedModelledSaving", 1)[1].split(
        "\n\nfunction renderCurrentRunValue", 1
    )[0]
    node_script = "function validatedModelledSaving" + helper + r'''
const assert = require("node:assert/strict");
const digest = `sha256:${"a".repeat(64)}`;
const evidence = {
  status: "PASS",
  verification_status: "PASS",
  current_pack_pilot_run: false,
  value_and_responsibility: {
    claim_ceiling: "SYNTHETIC_CONTROLLED_VALUE_PROOF",
    cost_model: {
      full_rebuild: 8.0,
      selective_rebase: 4.05,
      saving_rate: 0.49375,
      unit: "NORMALIZED_ACTION_COST",
      evidence_class: "MODELLED_COUNTERFACTUAL",
      enterprise_roi: "NOT_RUN",
      receipt_digest: digest,
      declared_scenario_envelope: {
        lower: 0.35,
        upper: 0.59875,
        interpretation: "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL",
      },
      stress_envelope: {
        lower: -0.1,
        upper: 0.59875,
        interpretation: "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL",
      },
    },
    metrics: [{
      metric_id: "selective_rebase_cost_saving_rate",
      status: "CALCULATED",
      maturity: "MODELLED",
      evidence_class: "MODELLED_COUNTERFACTUAL",
      unit: "RATIO",
      value: 0.49375,
      numerator: 3.95,
      denominator: 8.0,
      full_rebuild_normalized_cost: 8.0,
      selective_rebase_normalized_cost: 4.05,
    }],
  },
};
assert.equal(validatedModelledSaving(evidence).cost.saving_rate, 0.49375);
for (const mutation of [
  (copy) => { copy.status = "FAIL"; },
  (copy) => { copy.verification_status = "FAIL"; },
  (copy) => { copy.current_pack_pilot_run = true; },
  (copy) => { copy.value_and_responsibility.cost_model.evidence_class = "OBSERVED"; },
  (copy) => { copy.value_and_responsibility.cost_model.enterprise_roi = 123; },
  (copy) => { copy.value_and_responsibility.cost_model.receipt_digest = "sha256:short"; },
  (copy) => { copy.value_and_responsibility.cost_model.unit = "HOURS"; },
  (copy) => { copy.value_and_responsibility.cost_model.saving_rate = 0.9; },
  (copy) => { copy.value_and_responsibility.metrics[0].value = 0.9; },
  (copy) => { copy.value_and_responsibility.metrics[0].numerator = 4.0; },
  (copy) => { copy.value_and_responsibility.metrics[0].denominator = 0; },
  (copy) => { copy.value_and_responsibility.cost_model.full_rebuild = "8"; },
]) {
  const copy = structuredClone(evidence);
  mutation(copy);
  assert.equal(validatedModelledSaving(copy), null);
}
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "renderCurrentRunValue(currentState || UNAVAILABLE_WORKSPACE_PROJECTION, evidence);" in javascript
    assert "不是本次实测、工时或金额" in javascript
    assert "非真实企业 ROI" in javascript
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    assert "不同需求形成不同领域智能体与审查智能体拓扑" in chinese_catalog
    assert "领域智能体与 Reviewer 拓扑" not in chinese_catalog


def test_operations_current_lane_uses_five_stage_fallback_until_terminal_projection_passes() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert "state.execution_activity.rows.filter((row) => sameRunActivityRow(row, execution.run_id))" in javascript
    assert "const hasReceipt = (row) => Boolean(row && row.receipt_digest);" in javascript
    assert 'row.plane === "ADMISSION" && row.status === "PASS" && hasReceipt(row)' in javascript
    assert "function nativeAgentTeamsFormationReceipt(state, rows, execution)" in javascript
    assert 'competition.status !== "PASS" || competition.run_id !== execution.run_id' in javascript
    assert 'row.run_id === execution.run_id && row.source_run_id === execution.run_id' in javascript
    assert 'row.action === "EXECUTE_NATIVE_TASKFLOW_CANDIDATE"' in javascript
    assert 'task.role === "REVIEWER"' in javascript
    assert 'task.candidate_only === true' in javascript
    assert 'Number(task.target_writes) === 0' in javascript
    assert 'task.role === "DOMAIN_WORKER"' in javascript
    assert 'String(row.status || "").toUpperCase() === "TRUSTED_COMPLETE"' in javascript
    assert 'trustedTaskRow(finalReviewerTask.id, "REVIEWER")' in javascript
    assert 'row.source_ref === finalReviewerAttemptRef' in javascript
    assert 'row.action === "REPLAN_THEN_ACCEPT_EXACT_PROVENANCE"' in javascript
    assert 'String(row.status || "").toUpperCase() === "PASS"' in javascript
    assert 'finalReviewerPassRow.receipt_digest === competition.summary_digest' in javascript
    assert 'const businessRunStarted = state.stage !== "EMPTY";' in javascript
    assert "const agentRow = businessRunStarted ? nativeAgentTeamsFormationReceipt(state, rows, execution) : null;" in javascript
    assert "agentTaskRows.every" not in javascript
    assert "exactFinanceToolInvocation(rows, execution.run_id, collaboration.tool, financeAttemptTwo)" in javascript
    assert "exactGtmSkillInvocation(rows, execution.run_id, collaboration.skill, gtmComposeTask)" in javascript
    assert 'row.plane === "FORMATION"' in javascript
    assert 'row.action === "FORM_DOMAIN_COALITION_CONTEXT_AND_QUOTE_V1"' in javascript
    assert '(row.plane === "FORMATION" || row.plane === "CONTROL")' not in javascript
    assert 'row.status === "COMPLETED"\n      && hasReceipt(row)' in javascript
    assert 'stage: "SOURCE", row: sourceRow' in javascript
    assert 'status: item.status || "WAITING"' in javascript
    assert "const displayedItems = terminalProjection" in javascript
    assert 't(terminalProjection ? "operations.sameRun.projectionLane" : "operations.sameRun.currentLane")' in javascript
    assert "displayedItems," in javascript
    assert 'text("ops-proof-title", currentObserved > 0 ? t("operations.sameRun.runLabel") : t("operations.sameRun.unavailable"));' in javascript
    assert 'exactAuditTitle("ops-proof-title", currentObserved > 0 ? execution.run_id : null);' in javascript
    assert "报价形成子链 · 5 段" in javascript
    assert "终态因果投影 · 7 层" in javascript
    assert "operations.sameRun.boundaryComplete" in javascript
    assert 'const currentRunComplete = currentRunValueProjection(state).status === "PASS";' in javascript
    assert 'activeCompetition(state) && currentObserved === currentItems.length' in javascript
    assert 't("operations.boundary.formation")' in javascript
    assert "本地运行记录 · 独立于当前任务" in javascript


def test_agent_nodes_keep_agent_output_separate_from_nested_invocation_receipts() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert "financeTwoNode.outputDigest = toolActivity.receipt_digest" not in javascript
    assert "outputDigest: skill.receipt_digest" not in javascript
    assert "receipt: toolActivity.receipt_digest" in javascript
    assert "receipt: skillActivity.receipt_digest" in javascript
    assert 'actorKind: overrides.actorKind || "agent"' in javascript
    assert 'node.actorKind === "agent"' in javascript
    assert 'String(node.role || "").includes("Agent")' not in javascript


def test_current_run_completion_copy_is_gated_by_terminal_receipts_and_business_events() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")

    assert 'const passed = chain.status === "PASS" && events > 0;' in javascript
    assert 'localizedText("event-chain-status", passed ? "PASS" : "WAITING");' in javascript
    assert 'exactAuditTitle("event-chain-detail");' in javascript
    assert 'const currentRunComplete = currentRunValueProjection(normalized).status === "PASS";' in javascript
    assert 'currentRunComplete ? "active.boundary.golden" : "active.boundary.goldenFormation"' in javascript
    assert "两次人工审批、选择性重构与终态回执尚未全部完成，不得视为完整业务闭环" in javascript
    assert "this is not a complete business loop" in javascript


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the AgentTeams truth-gate contract")
def test_agentteams_current_lane_follows_final_bound_revision_not_superseded_attempts() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    start = javascript.index("function nativeAgentTeamsFormationReceipt")
    end = javascript.index("\n\nconst OTLP_AUDIT_FIELD_LABELS", start)
    helper = javascript[start:end]
    node_script = helper + r"""
const assert = require("node:assert/strict");
const runId = "run:golden-competition:test";
const managerId = "task:manager";
const productId = "task:product-a1";
const financeA1 = "task:finance-a1";
const financeA2 = "task:finance-a2";
const reviewerA2 = "task:reviewer-a2";
const state = {
  competition_evidence: {
    status: "PASS",
    run_id: runId,
    summary_digest: "sha256:reviewer-pass",
    agent_collaboration: {
      reviewer: { attempt_2: { verdict: "PASS" } },
      orchestration_plan: {
        run_id: runId,
        status: "COMPLETED",
        digest: "sha256:plan",
        project_id: managerId,
        tasks: [
          { id: managerId, candidate_only: true, target_writes: 0, digest: "sha256:manager-task" },
          { id: productId, role: "DOMAIN_WORKER", candidate_only: true, target_writes: 0, binding_digest: "sha256:product-binding", digest: "sha256:product-task" },
          { id: financeA1, role: "DOMAIN_WORKER", candidate_only: true, target_writes: 0, binding_digest: "sha256:finance-a1-binding", digest: "sha256:finance-a1-task" },
          { id: financeA2, role: "DOMAIN_WORKER", candidate_only: true, target_writes: 0, binding_digest: "sha256:finance-a2-binding", digest: "sha256:finance-a2-task" },
          { id: reviewerA2, role: "REVIEWER", attempt: 2, candidate_only: true, target_writes: 0, binding_digest: "sha256:reviewer-binding", digest: "sha256:reviewer-task", depends_on: [productId, financeA2] },
        ],
      },
    },
  },
};
const nativeRow = (plane, sourceRef, status, digest) => ({
  run_id: runId,
  source_run_id: runId,
  plane,
  action: "EXECUTE_NATIVE_TASKFLOW_CANDIDATE",
  permission: "CANDIDATE_ONLY",
  target_writes: 0,
  source_ref: sourceRef,
  status,
  receipt_digest: digest,
});
const rows = [
  nativeRow("AGENTTEAMS", managerId, "TRUSTED_COMPLETE", "sha256:manager-run"),
  nativeRow("AGENTTEAMS", productId, "TRUSTED_COMPLETE", "sha256:product-run"),
  nativeRow("AGENTTEAMS", financeA1, "ABSTAIN", "sha256:finance-a1-run"),
  nativeRow("AGENTTEAMS", financeA2, "TRUSTED_COMPLETE", "sha256:finance-a2-run"),
  nativeRow("REVIEWER", reviewerA2, "TRUSTED_COMPLETE", "sha256:reviewer-run"),
  {
    run_id: runId,
    source_run_id: runId,
    plane: "REVIEWER",
    action: "REPLAN_THEN_ACCEPT_EXACT_PROVENANCE",
    permission: "CANDIDATE_ONLY",
    target_writes: 0,
    source_ref: "reviewer:attempt-2",
    status: "PASS",
    receipt_digest: "sha256:reviewer-pass",
  },
];
const execution = { run_id: runId };
const receipt = nativeAgentTeamsFormationReceipt(state, rows, execution);
assert.equal(receipt.receipt_digest, "sha256:reviewer-pass");
assert.equal(rows.find((row) => row.source_ref === financeA1).status, "ABSTAIN");

const wrongRun = structuredClone(state);
wrongRun.competition_evidence.agent_collaboration.orchestration_plan.run_id = "run:other";
assert.equal(nativeAgentTeamsFormationReceipt(wrongRun, rows, execution), null);

const missingBinding = structuredClone(state);
delete missingBinding.competition_evidence.agent_collaboration.orchestration_plan.tasks[3].binding_digest;
assert.equal(nativeAgentTeamsFormationReceipt(missingBinding, rows, execution), null);

const missingFinalReceipt = rows.filter((row) => row.source_ref !== financeA2);
assert.equal(nativeAgentTeamsFormationReceipt(state, missingFinalReceipt, execution), null);

const reviewerLifecycleInWrongPlane = rows.map((row) => row.source_ref === reviewerA2 ? { ...row, plane: "AGENTTEAMS" } : row);
assert.equal(nativeAgentTeamsFormationReceipt(state, reviewerLifecycleInWrongPlane, execution), null);

const wrongVerdictAttempt = rows.map((row) => row.action === "REPLAN_THEN_ACCEPT_EXACT_PROVENANCE" ? { ...row, source_ref: "reviewer:attempt-1" } : row);
assert.equal(nativeAgentTeamsFormationReceipt(state, wrongVerdictAttempt, execution), null);

const reviewerRejected = structuredClone(state);
reviewerRejected.competition_evidence.agent_collaboration.reviewer.attempt_2.verdict = "REPLAN";
assert.equal(nativeAgentTeamsFormationReceipt(reviewerRejected, rows, execution), null);
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_ui_never_reconstructs_current_success_from_compatibility_or_stale_archive_state() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    assert ".filter((row) => sameRunActivityRow(row, execution.run_id) && row.receipt_digest)" in javascript
    assert "Compatibility fallback for a pre-Spec-055 server" not in javascript
    assert 'reviewer.attempt_2 && reviewer.attempt_2.verdict || "NOT_OBSERVED"' in javascript
    assert "retainedActions.find" in javascript
    assert "candidate.ok === true" in javascript
    assert "currentGoldenEvidenceStatus" not in javascript
    assert 'const terminal = currentRunValueProjection(state).status === "PASS";' in javascript
    assert "变化到达后先显示零写预演与候选影响" in html
    assert 'runAwaitingEmployee: "等待员工发起工作"' in shell
    assert 'runAwaitingEmployee: "Business run starts after employee request"' in shell
    assert 'state.stage === "EMPTY"\n          ? selected.runAwaitingEmployee' in shell
    assert "运行编号已预留" not in shell
    assert "Execution ID reserved" not in shell
    assert ': runSummary(state)' in shell
    assert 'currentRunValueProjection(state).status === "PASS") return copy().runComplete' in shell


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Skill source editing")
def test_skill_source_revision_updates_nested_metadata_version() -> None:
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    renderer = "function replaceSkillFrontmatterVersion" + javascript.split(
        "function replaceSkillFrontmatterVersion", 1
    )[1].split("function beginSkillRevision", 1)[0]
    node_script = f"""
{renderer}
const nested = `---\nname: quote-compose\nmetadata:\n  version: "1.2.3"\n  owner: quote\n---\nbody`;
const legacy = `---\nname: quote-compose\nversion: 0.9.0\n---\nbody`;
const unrelated = `---\nname: quote-compose\nmetadata:\n  owner: quote\n---\nbody`;
console.log(JSON.stringify({{
  nested: replaceSkillFrontmatterVersion(nested, "1.2.4"),
  legacy: replaceSkillFrontmatterVersion(legacy, "0.9.1"),
  unrelated: replaceSkillFrontmatterVersion(unrelated, "3.0.0"),
}}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", node_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert 'metadata:\n  version: "1.2.4"' in result["nested"]
    assert "version: 0.9.1" in result["legacy"]
    assert result["unrelated"] == "---\nname: quote-compose\nmetadata:\n  owner: quote\n---\nbody"


def test_skill_center_reads_exact_published_source_and_only_saves_candidate_drafts() -> None:
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    javascript = (CONSOLE / "app.js").read_text(encoding="utf-8")
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")

    for element_id in (
        "skill-source-catalog",
        "skill-source-dialog",
        "skill-source-editor",
        "skill-source-proposed-version",
        "skill-source-revise",
        "skill-source-save",
        "skill-source-cancel",
        "skill-source-close",
        "skill-source-draft-list",
    ):
        assert f'id="{element_id}"' in html

    skill_page = html.split('id="cockpit-skills"', 1)[1].split(
        'id="cockpit-operations"', 1
    )[0]
    assert 'id="skill-source-dialog"' in skill_page
    assert 'id="skill-source-editor" readonly' in skill_page

    source_functions = javascript.split("function normalizeSkillSourceCatalog", 1)[1].split(
        "async function decideExperience", 1
    )[0]
    assert ".innerHTML" not in source_functions
    assert "insertAdjacentHTML" not in source_functions
    assert "editor.value = skillPackage.content" in source_functions
    assert "editor.readOnly = true" in source_functions
    assert "editor.scrollTop = 0" in source_functions
    assert 'api("/api/workspace/skills")' in javascript
    assert "/api/workspace/skills/${encodeURIComponent(skillPackage.name)}/drafts" in javascript
    assert '"X-OrgRebase-Actor": "human:skill-steward"' in javascript
    for boundary in (
        'draft.evaluation_status === "NOT_EVALUATED"',
        'draft.release_status === "NOT_RELEASED"',
        "draft.executable === false",
        "draft.registry_writes === 0",
        "draft.canonical_target_writes === 0",
    ):
        assert boundary in javascript
    for visible_fact in (
        'skillSourceElement("code", "", draft.evaluation_status)',
        'skillSourceElement("code", "", draft.release_status)',
        '`executable=${String(draft.executable)}`',
        '`registry writes=${draft.registry_writes}`',
    ):
        assert visible_fact in javascript

    assert "下次任务固定版本" not in html + javascript + shell
    assert "Pinned version in a future task" not in html + javascript + shell
    assert "受控灰度试调用" in html + javascript + shell


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the admission projection")
def test_workspace_shell_uses_same_run_deployment_gate_for_optional_and_required_oac() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    copy_source = shell[shell.index("  const COPY ="):shell.index("  const byId =")]
    gate_source = shell[shell.index("  function organizationContractGate("):shell.index("  function setExactText(")]
    render_source = shell[shell.index("  function renderLifecycleRail("):shell.index("  function setCopy(")]
    harness = copy_source + gate_source + render_source + r'''
const assert = require("node:assert/strict");
let language = "zh-CN";
let currentState = null;
let stateAvailability = "ready";
let currentRoute = "quote";
const copy = () => COPY[language];
const nodes = new Map();
const byId = (id) => {
  if (!nodes.has(id)) nodes.set(id, { dataset: {}, textContent: "", hidden: false });
  return nodes.get(id);
};
const rail = ["onboarding", "quote", "assurance"].map((step) => ({
  dataset: { lifecycleStep: step },
  marker: { textContent: "" },
  querySelector() { return this.marker; },
}));
const document = { querySelectorAll: () => rail };
const base = {
  stage: "CURRENT", business_complete: false,
  execution: { run_id: "run:current", oac_activation: { status: "NOT_USED_IN_THIS_RUN" } },
  workspace_gate: {
    schema_version: "orgrebase.workspace-oac-gate.v1", mode: "optional",
    execution_run_id: "run:current", requires_oac_admission: false,
    form_allowed: true, status: "READY_TO_FORM", activation_binding_digest: null,
    consumption_receipt_digest: null,
  },
};
function render(state) {
  currentState = state;
  renderTaskPrerequisite(state);
  renderLifecycleRail(state);
}
for (const mode of ["optional", "off"]) {
  const state = structuredClone(base);
  state.workspace_gate.mode = mode;
  for (language of ["zh-CN", "en"]) {
    render(state);
    assert.equal(organizationContractReady(state), true);
    assert.equal(byId("task-prerequisite-banner").dataset.state, "ready");
    assert.equal(byId("task-prerequisite-body").textContent, copy().prerequisiteOptionalBody);
    assert.equal(byId("task-prerequisite-action").hidden, true);
    assert.equal(rail[0].dataset.state, "done");
    assert.equal(rail[1].dataset.state, "current");
    assert.equal(onboardingContext(state).label, copy().onboardingOptional);
    assert.notEqual(onboardingContext(state).label, copy().onboardingReady);
  }
}
const required = structuredClone(base);
Object.assign(required.workspace_gate, {
  mode: "required", requires_oac_admission: true, form_allowed: false,
  status: "BLOCKED_PENDING_OAC",
});
render(required);
assert.equal(organizationContractReady(required), false);
assert.equal(byId("task-prerequisite-banner").dataset.state, "blocked");
assert.equal(byId("task-prerequisite-body").textContent, copy().prerequisiteBlockedBody);
assert.equal(byId("task-prerequisite-action").hidden, false);
assert.equal(rail[1].dataset.state, "locked");
Object.assign(required.workspace_gate, {
  form_allowed: true, status: "READY_TO_FORM", activation_binding_digest: "sha256:binding",
});
render(required);
assert.equal(organizationContractReady(required), true);
assert.equal(byId("task-prerequisite-body").textContent, copy().prerequisiteReadyBody);
required.workspace_gate.status = "CONSUMED_BY_QUOTE_FORMATION";
assert.equal(organizationContractReady(required), false, "a consumption claim requires its receipt");
required.workspace_gate.consumption_receipt_digest = "sha256:consumption";
assert.equal(organizationContractReady(required), true);
required.workspace_gate.form_allowed = false;
required.workspace_gate.reason_code = "OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED";
render(required);
assert.equal(organizationContractEstablished(required), true, "new execution restrictions cannot erase consumed admission");
assert.equal(organizationContractReady(required), false, "historical admission cannot authorize another task");
assert.equal(byId("task-prerequisite-banner").dataset.state, "ready");
assert.equal(byId("task-prerequisite-action").hidden, true);
assert.equal(rail[0].dataset.state, "done");
assert.equal(rail[1].dataset.state, "current");
for (const mutate of [
  (state) => { delete state.workspace_gate; },
  (state) => { state.workspace_gate.schema_version = "unknown"; },
  (state) => { state.workspace_gate.execution_run_id = "run:foreign"; },
  (state) => { state.workspace_gate.mode = "unknown"; },
  (state) => { state.workspace_gate.requires_oac_admission = true; },
  (state) => { state.workspace_gate.form_allowed = "true"; },
  (state) => { state.workspace_gate.status = "BLOCKED_PENDING_OAC"; },
  (state) => { delete state.execution; },
]) {
  const state = structuredClone(base);
  mutate(state);
  render(state);
  assert.equal(organizationContractReady(state), false);
  assert.equal(byId("task-prerequisite-banner").dataset.state, "checking");
  assert.equal(byId("task-prerequisite-body").textContent, copy().prerequisiteCheckingBody);
  assert.equal(byId("task-prerequisite-action").hidden, true);
  assert.equal(rail[1].dataset.state, "locked");
}
stateAvailability = "loading";
renderTaskPrerequisite(base);
assert.equal(byId("task-prerequisite-banner").dataset.state, "checking");
'''
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", harness],
        cwd=ROOT, check=False, capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '/api/workspace/state' not in shell
    assert "currentOac" not in shell
