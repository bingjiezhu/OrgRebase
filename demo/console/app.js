const el = (id) => document.getElementById(id);
const buttons = {
  workspace: el("workspace-button"),
  preview: el("preview-button"),
  apply: el("apply-button"),
  run: el("run-button"),
  reset: el("reset-button"),
};

let toastTimer;
let releaseFacts = null;

async function loadReleaseFacts() {
  try {
    const response = await fetch("/api/release-facts");
    if (!response.ok) return;
    releaseFacts = await response.json();
    renderEvidenceStatus();
  } catch (_) {
    releaseFacts = null;
  }
}

function setSeal(state, mark, caption) {
  const node = el("integrity-seal");
  node.dataset.state = state;
  node.innerHTML = `<b>${mark}</b><small>${caption}</small>`;
}

function evidenceRows() {
  if (!releaseFacts) return '<div class="receipt-line"><span>证据事实</span><b>UNAVAILABLE</b></div>';
  const workspace = releaseFacts.workspace;
  const coreLive = releaseFacts.core_change_advisory_live_agentteams;
  const candidates = releaseFacts.candidate_control_plane;
  const orchestration = releaseFacts.agent_orchestration;
  const skill = releaseFacts.skill_governance;
  return `<div class="receipt-line"><span>Workspace AgentTeams</span><b>${workspace.agentteams_static} / ${workspace.agentteams_live}</b></div>
    <div class="receipt-line"><span>Core change-advisory</span><b>${coreLive.evidence_class}</b></div>
    <div class="receipt-line"><span>编译后的 Agent DAG</span><b>${orchestration.task_count} 项 · ${orchestration.status}</b></div>
    <div class="receipt-line"><span>绑定 Skill 动作</span><b>${skill.bound_action_candidates} · ${skill.release_state}</b></div>
    <div class="receipt-line"><span>live 候选已控</span><b>拒绝 ${candidates.rejected} · 写入 ${candidates.target_writes}</b></div>`;
}

function renderEvidenceStatus() {
  if (el("receipt-state").textContent !== "PENDING") return;
  el("receipt-panel").innerHTML = `${evidenceRows()}
    <div class="receipt-line"><span>规范写者</span><b>控制面</b></div>
    <div class="skill-flow"><span>v1.2</span><i>FAIL</i><b>→</b><span>v1.3</span><i class="pass">PASS</i><b>→</b><span>CANARY</span></div>
    <p class="boundary">合成企业数据与本地确定性证据。Workspace live AgentTeams 在无关联证据时保持 NOT_RUN；Core 五 Agent live 收据不升级 Workspace。</p>`;
}

function toast(message) {
  const node = el("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2200);
}

async function request(path) {
  Object.values(buttons).forEach((button) => (button.disabled = true));
  try {
    const response = await fetch(path, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail?.message || "请求失败");
    return payload;
  } finally {
    buttons.workspace.disabled = false;
    buttons.preview.disabled = false;
    buttons.run.disabled = false;
    buttons.reset.disabled = false;
  }
}

function impactTone(classification) {
  if (classification === "UNKNOWN") return "unknown";
  if (classification === "UNAFFECTED_WITHIN_DECLARED_BOUNDARY") return "bounded";
  if (classification === "REQUALIFICATION_REQUIRED") return "skill";
  return "affected";
}

function impactCaption(classification) {
  if (classification === "UNKNOWN") return "证据不足";
  if (classification === "UNAFFECTED_WITHIN_DECLARED_BOUNDARY") return "证明可留";
  if (classification === "REQUALIFICATION_REQUIRED") return "Skill 重验";
  return "必须重建";
}

function renderPreview(preview, minimalCertificate) {
  el("affected-count").textContent = preview.counts.affected_hard;
  el("bounded-count").textContent = preview.counts.bounded_unaffected;
  el("unknown-count").textContent = preview.counts.unknown;
  el("skill-count").textContent = preview.counts.skill_requalification;
  el("stage-title").textContent = "影响边界已证明";
  el("change-digest").textContent = preview.revision_lock.change_set_digest;
  setSeal("locked", "锁", "LOCKED");
  el("impact-list").innerHTML = preview.results
    .map((item) => {
      const evidence = item.proof_path.length
        ? item.proof_path.map((step) => step.edge_id).join(" → ")
        : item.reason_code;
      return `<div class="impact-item ${impactTone(item.classification)}">
        <span class="dot"></span>
        <div><strong>${item.label}</strong><small>${evidence}</small></div>
        <em>${impactCaption(item.classification)}</em>
      </div>`;
    })
    .join("");
  el("receipt-state").textContent = "CERTIFIED";
  el("receipt-panel").innerHTML = `
    <div class="receipt-line"><span>影响证书</span><b>${preview.certificates.length} 份已核验</b></div>
    <div class="receipt-line"><span>最小 Rebase 证书</span><b>${minimalCertificate.digest.slice(0, 18)}…</b></div>
    <div class="receipt-line"><span>漏重建</span><b>FAIL CLOSED</b></div>
    <div class="receipt-line"><span>多重建</span><b>FAIL CLOSED</b></div>
    ${evidenceRows()}
    <p class="boundary">VMRC 绑定 ChangeSet、Preview、revision lock 与逐目标 ImpactCertificate。</p>`;
  buttons.apply.disabled = false;
}

function renderReceipt(receipt, eventChain) {
  const metrics = receipt.metrics;
  el("stage-title").textContent = "选择性 Rebase 已完成";
  el("receipt-state").textContent = receipt.status;
  el("receipt-panel").innerHTML = `
    <div class="receipt-line"><span>主张已晋升</span><b>v7 → v8</b></div>
    <div class="receipt-line"><span>已 Rebase 工作</span><b>${metrics.work_items_rebased}</b></div>
    <div class="receipt-line"><span>证明保留</span><b>${metrics.bounded_unaffected}</b></div>
    <div class="receipt-line"><span>升级 UNKNOWN</span><b>${metrics.unknown}</b></div>
    <div class="receipt-line"><span>VMRC 绑定</span><b>${receipt.minimal_rebase_certificate_digest.slice(0, 18)}…</b></div>
    <div class="receipt-line"><span>未授权披露</span><b>${metrics.unauthorized_disclosures}</b></div>
    <div class="receipt-line"><span>事件链</span><b>${eventChain.status} · ${eventChain.events}</b></div>
    <div class="skill-flow"><span>v1.2</span><i>FAIL</i><b>→</b><span>v1.3</span><i class="pass">PASS</i><b>→</b><span>CANARY</span></div>
    <p class="boundary">回执 ${receipt.digest.slice(0, 23)}… · LOCAL_DETERMINISTIC</p>`;
  setSeal("verified", "核", "VERIFIED");
  buttons.apply.disabled = false;
}

function previewCounts(preview) {
  const counts = preview && preview.counts ? preview.counts : {};
  return {
    affected: counts.affected_hard ?? "—",
    bounded: counts.bounded_unaffected ?? "—",
    unknown: counts.unknown ?? "—",
    skill: counts.skill_requalification ?? "—",
  };
}

function renderWorkspace(payload, agentteams) {
  const quote = payload.final_quote || {};
  const fields = quote.payload || {};
  const launchPreview = payload.launch_change && payload.launch_change.preview;
  const counts = previewCounts(launchPreview);
  const chain = payload.event_chain || {};
  const agentteamsStatus = (agentteams && agentteams.status) || "NOT_RUN";
  el("affected-count").textContent = counts.affected;
  el("bounded-count").textContent = counts.bounded;
  el("unknown-count").textContent = counts.unknown;
  el("skill-count").textContent = counts.skill;
  el("stage-title").textContent = "Workspace 报价闭环已完成";
  el("change-digest").textContent = `${quote.id || "work:quote_acme"}@${quote.version || "v3"}`;
  setSeal("local", "本", "LOCAL");
  el("impact-list").innerHTML = `
    <div class="impact-item affected"><span class="dot"></span><div><strong>报价 ${quote.version || "v3"}</strong><small>${fields.launch_date || "2026-09-15"} · ${fields.currency || "EUR"}</small></div><em>终稿</em></div>
    <div class="impact-item bounded"><span class="dot"></span><div><strong>最小联盟</strong><small>${(payload.formation && payload.formation.coalition_plan_ref) || "产品 / 法务 / 财务 / 销售"}</small></div><em>已组建</em></div>
    <div class="impact-item unknown"><span class="dot"></span><div><strong>Workspace AgentTeams live</strong><small>无关联 K8s / Matrix / provider 证据时不得称为 live</small></div><em>${agentteamsStatus}</em></div>
    <div class="impact-item skill"><span class="dot"></span><div><strong>进程重启后币种 Rebase</strong><small>graph ${(payload.final_graph_pointer && payload.final_graph_pointer.version) || "v3"}</small></div><em>已重启</em></div>`;
  el("receipt-state").textContent = "LOCAL_DETERMINISTIC";
  el("receipt-panel").innerHTML = `
    <div class="receipt-line"><span>终稿报价</span><b>${quote.version || "v3"} · ${fields.launch_date || "—"} · ${fields.currency || "—"}</b></div>
    <div class="receipt-line"><span>事件链</span><b>${chain.status || "PASS"}</b></div>
    <div class="receipt-line"><span>Workspace AgentTeams</span><b>${agentteamsStatus}</b></div>
    <div class="receipt-line"><span>规范写者</span><b>控制面</b></div>
    <p class="boundary">此按钮调用 POST /api/demo/workspace/quote-to-rebase。这是本地确定性闭环，不是 Vertex live，也不把 Core 五 Agent live 收据算进 Workspace。</p>`;
  buttons.apply.disabled = true;
}

buttons.workspace.addEventListener("click", async () => {
  try {
    const payload = await request("/api/demo/workspace/quote-to-rebase");
    let agentteams = { status: "NOT_RUN" };
    try {
      const statusResponse = await fetch("/api/demo/workspace/agentteams-status");
      if (statusResponse.ok) agentteams = await statusResponse.json();
    } catch (_) {
      agentteams = { status: "NOT_RUN" };
    }
    renderWorkspace(payload, agentteams);
    toast("报价 v3 已形成（本地确定性）");
  } catch (error) { toast(error.message); }
});

buttons.preview.addEventListener("click", async () => {
  try {
    const payload = await request("/api/demo/preview");
    renderPreview(payload.preview, payload.minimal_rebase_certificate);
    toast("预演完成：目标状态零写入");
  } catch (error) { toast(error.message); }
});

buttons.apply.addEventListener("click", async () => {
  try {
    const payload = await request("/api/demo/apply");
    renderReceipt(payload.receipt, payload.event_chain);
    toast("批准完成：回执已生成并验证");
  } catch (error) { toast(error.message); }
});

buttons.run.addEventListener("click", async () => {
  try {
    const payload = await request("/api/demo/run");
    renderPreview(payload.preview, payload.minimal_rebase_certificate);
    renderReceipt(payload.receipt, payload.event_chain);
    toast("Core 全链路已执行完毕");
  } catch (error) { toast(error.message); }
});

buttons.reset.addEventListener("click", async () => {
  try {
    await request("/api/demo/reset");
    ["affected-count", "bounded-count", "unknown-count", "skill-count"].forEach((id) => (el(id).textContent = "—"));
    el("stage-title").textContent = "准备就绪";
    el("change-digest").textContent = "等待预演";
    setSeal("wait", "待", "WAIT");
    el("impact-list").innerHTML = '<div class="empty">先跑报价闭环，或先预演影响。</div>';
    el("receipt-state").textContent = "PENDING";
    renderEvidenceStatus();
    buttons.apply.disabled = true;
    toast("演示状态已重置");
  } catch (error) { toast(error.message); }
});

loadReleaseFacts();
