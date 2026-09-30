(() => {
  "use strict";
  const client = window.OrgRebaseClient;
  const host = document.getElementById("task-changes-flow");
  if (!client || !host) return;
  const panel = document.createElement("section");
  panel.id = "deliverable-set-panel";
  panel.className = "change-workbench";
  panel.hidden = true;
  panel.innerHTML = `<header class="change-workbench-header"><div><h2 id="deliverable-set-title"></h2><p id="deliverable-set-summary"></p></div><div class="change-workbench-actions"><button id="deliverable-set-refresh" class="button button-secondary" type="button"></button><button id="deliverable-set-download" class="button button-secondary" type="button" disabled></button></div></header><h3 id="deliverable-set-current-title"></h3><p id="deliverable-set-current-notice" role="status" aria-live="polite"></p><div id="deliverable-set-current"></div><h3 id="deliverable-set-review-title"></h3><p id="deliverable-set-notice" role="status" aria-live="polite"></p><div id="deliverable-set-members"></div>`;
  const changeWorkbench = document.getElementById("change-workbench");
  if (changeWorkbench && typeof changeWorkbench.after === "function") changeWorkbench.after(panel);
  else host.append(panel);
  const node = id => document.getElementById(id);
  const text = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  let selectedEvent = null;
  let busy = false;
  let refreshGeneration = 0;
  let contextGeneration = 0;
  let profileGeneration = 0;
  let profileReady = false;

  function selectedEventId() {
    try {
      return JSON.parse(document.querySelector("#change-audit pre")?.textContent || "{}").event_id || null;
    } catch (_) { return null; }
  }

  function labels() {
    node("deliverable-set-title").textContent = text("报价与折扣说明", "Quote and discount memo");
    node("deliverable-set-summary").textContent = text("每位成果负责人只签署自己的精确范围；来源批准不能替代成果批准。", "Each deliverable owner signs only their exact scope; source approval does not replace output approval.");
    node("deliverable-set-refresh").textContent = text("刷新成果状态", "Refresh deliverables");
    node("deliverable-set-download").textContent = text("下载报价与折扣说明", "Download Quote and Memo");
    node("deliverable-set-current-title").textContent = text("当前正式成果", "Current committed deliverables");
    node("deliverable-set-review-title").textContent = text("本次变化的成果审阅", "Deliverable review for this change");
  }

  function safeFailure(error) {
    if (error?.status === 401) return text("会话已失效，请重新登录后刷新成果。未确认的操作不会自动重发。", "Your session expired. Sign in and refresh deliverables; unconfirmed commands are not replayed.");
    if (error?.status === 403) return text("当前身份无法读取或操作这些成果，请核对工作区授权后刷新。", "This identity cannot read or act on these deliverables. Review workspace access, then refresh.");
    if (error?.code === "WORKSPACE_DELIVERABLE_SET_NOT_FORMED") return text("报价与折扣说明尚未形成，请先完成任务启动，再刷新成果。", "Quote and Memo have not been formed. Start the task, then refresh deliverables.");
    return text("成果状态未能确认，已清空上次读取的内容。请刷新核对；操作不会自动重发。", "Deliverable state could not be confirmed. Previous content was cleared. Refresh to reconcile; commands are not replayed.");
  }

  const dispositionLabel = (value, effective) => ({
    REBUILD: effective ? text("已更新", "Updated") : text("需要更新", "Update required"),
    PRESERVE_WITHIN_BOUNDARY: text("有证据保留", "Preserved with evidence"),
    HOLD_FOR_REVIEW: text("待核查", "Review required"),
  })[value] || value;
  const decisionLabel = value => ({
    APPROVED: text("已批准", "Approved"),
    REJECTED: text("已拒绝", "Rejected"),
  })[value] || text("待签署", "Pending signature");
  const approvalLabel = value => ({
    INCOMPLETE: text("等待其他负责人", "Awaiting other owners"),
    COMPLETE: text("成果批准已齐全", "All deliverable approvals complete"),
    REJECTED: text("当前批次已拒绝", "Current batch rejected"),
  })[value] || text("等待成果负责人决定", "Awaiting deliverable owners");
  const ownerLabel = actorId => {
    const actor = (client.session()?.actors || []).find(item => item.actor_id === actorId);
    return actor ? text(actor.label || actorId, actor.label_en || actorId) : actorId;
  };
  const limitationLabel = value => ({
    NOT_A_POLICY_APPROVAL: text("折扣说明不替代规则批准", "The memo does not approve pricing policy"),
    NO_FX_CONVERSION: text("不进行汇率换算", "No currency conversion"),
    NO_EXTERNAL_EFFECT: text("不写入外部系统", "No external system writes"),
  })[value] || value;

  function reviewFacts(payload) {
    const pricing = payload?.pricing || {};
    const comparison = payload?.comparison || {};
    if (payload?.deliverable_kind === "DISCOUNT_MEMO") return [
      [text("当前折扣", "Current discount"), pricing.discount_rate_bps == null ? "—" : `${Number(pricing.discount_rate_bps) / 100}%`],
      [text("折扣金额", "Discount amount"), pricing.discount_amount ? `${pricing.currency} ${pricing.discount_amount}` : "—"],
      [text("折后未税金额", "Net amount"), pricing.net_amount ? `${pricing.currency} ${pricing.net_amount}` : "—"],
      [text("含税总额", "Total"), pricing.total ? `${pricing.currency} ${pricing.total}` : "—"],
      [text("与上次总额差", "Total change"), comparison.total_delta ? `${pricing.currency} ${comparison.total_delta}` : text("首次形成", "Initial formation")],
      [text("说明边界", "Memo limitations"), (payload.limitations || []).map(limitationLabel).join(" · ") || "—"],
    ];
    return [
      [text("企业方案", "Product plan"), payload?.product_plan || "—"],
      [text("上线日期", "Launch date"), payload?.launch_date || "—"],
      [text("币种", "Currency"), payload?.currency || pricing.currency || "—"],
      [text("报价总额", "Quote total"), pricing.total ? `${pricing.currency} ${pricing.total}` : "—"],
    ];
  }

  function card(member, decision, review, effective) {
    const article = document.createElement("article"); article.className = "change-detail";
    const title = document.createElement("h3");
    title.textContent = member.deliverable_kind === "QUOTE" ? text("企业报价", "Enterprise quote") : text("折扣复核说明", "Discount review memo");
    const facts = document.createElement("dl");
    for (const [label, value] of [
      [text("本次处理", "This change"), dispositionLabel(member.disposition, effective)],
      [text("负责人", "Owner"), ownerLabel(member.owner_id)],
      ...reviewFacts(review?.safe_payload),
      [text("决定", "Decision"), member.disposition === "PRESERVE_WITHIN_BOUNDARY"
        ? text("不需重新签署 · 保留原版本", "No new signature required · Earlier version preserved")
        : decisionLabel(decision?.decision)],
    ]) {
      const row = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = label; dd.textContent = value || "—"; row.append(dt, dd); facts.append(row);
    }
    const audit = document.createElement("details"), summary = document.createElement("summary"), code = document.createElement("code");
    summary.textContent = text("查看候选引用与摘要", "Show candidate reference and digest");
    code.textContent = `${member.candidate_ref}\n${member.candidate_payload_digest}`;
    audit.append(summary, code); article.append(title, facts, audit);
    return article;
  }

  function currentCard(item) {
    const article = document.createElement("article"); article.className = "change-detail";
    const title = document.createElement("h4");
    title.textContent = item.payload.deliverable_kind === "QUOTE" ? text("企业报价", "Enterprise quote") : text("折扣复核说明", "Discount review memo");
    const facts = document.createElement("dl");
    for (const [label, value] of [[text("当前版本", "Current version"), item.version], [text("负责人", "Owner"), ownerLabel(item.payload.owner)], ...reviewFacts(item.payload)]) {
      const row = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = label; dd.textContent = value || "—"; row.append(dt, dd); facts.append(row);
    }
    article.append(title, facts);
    return article;
  }

  function clearSensitive() {
    ++refreshGeneration;
    ++contextGeneration;
    ++profileGeneration;
    profileReady = false;
    selectedEvent = null;
    node("deliverable-set-current").replaceChildren();
    node("deliverable-set-current-notice").textContent = "";
    node("deliverable-set-members").replaceChildren();
    node("deliverable-set-notice").textContent = "";
    node("deliverable-set-download").disabled = true;
  }

  function setButtonsDisabled(value) {
    for (const button of panel.querySelectorAll("button")) button.disabled = value || button.id === "deliverable-set-download" && !profileReady;
  }

  async function decide(value, decision, eventId) {
    if (busy || !eventId || eventId !== selectedEvent) return;
    const context = contextGeneration;
    busy = true;
    setButtonsDisabled(true);
    try {
      const digest = String(value.candidate_set.digest || "").replace("sha256:", "").slice(0, 24);
      await client.json(`/api/workspace/approve/${encodeURIComponent(eventId)}/deliverable-set`, {
        method: "POST",
        body: {
          operation_id: `ui-deliverable-${digest}-${decision.toLowerCase()}`,
          preview_digest: value.preview_digest,
          decision,
        },
      });
      if (context !== contextGeneration) return;
      if (eventId === selectedEvent) await refreshChange();
      if (context !== contextGeneration) return;
      window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", { detail: { source: "deliverable-set" } }));
    } catch (error) {
      if (context !== contextGeneration || eventId !== selectedEvent || error?.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
      clearSensitive();
      node("deliverable-set-current-notice").textContent = safeFailure(error);
    } finally {
      busy = false;
      setButtonsDisabled(false);
    }
  }

  async function refreshChange() {
    const eventId = selectedEventId();
    const generation = ++refreshGeneration;
    selectedEvent = eventId;
    const mount = node("deliverable-set-members"); mount.replaceChildren();
    if (!eventId) {
      node("deliverable-set-notice").textContent = text("当前正式成果见上方。选择一项已预演变化后，可分别审阅候选与签署自己的范围。", "Current committed deliverables appear above. Select a previewed change to inspect candidates and sign your own scope.");
      return;
    }
    try {
      const value = await client.json(`/api/workspace/deliverable-set/changes/${encodeURIComponent(eventId)}`);
      if (generation !== refreshGeneration || eventId !== selectedEvent || eventId !== selectedEventId()) return;
      const decisions = new Map((value.decisions || []).map(item => [item.owner_id, item]));
      const reviews = new Map((value.review_members || []).map(item => [item.object_id, item]));
      for (const member of value.candidate_set?.members || []) mount.append(card(member, decisions.get(member.owner_id), reviews.get(member.object_id), Boolean(value.outcome)));
      const current = value.current_principal_owner_id;
      const actions = new Set(value.allowed_actions || []);
      if (current && (value.pending_owner_ids || []).includes(current) && actions.size) {
        const controls = document.createElement("div"); controls.className = "change-workbench-actions";
        for (const [decision, zh, en] of [["APPROVED", "批准我的成果范围", "Approve my deliverable scope"], ["REJECTED", "拒绝我的成果范围", "Reject my deliverable scope"]]) {
          const action = decision === "APPROVED" ? "APPROVE" : "REJECT";
          if (!actions.has(action)) continue;
          const button = document.createElement("button"); button.type = "button"; button.className = "button button-secondary"; button.textContent = text(zh, en);
          button.disabled = busy;
          button.addEventListener("click", () => decide(value, decision, eventId)); controls.append(button);
        }
        mount.append(controls);
      }
      node("deliverable-set-notice").textContent = value.outcome
        ? text("两项成果已生效", "Both deliverables are effective")
        : !value.source_approval_digest
        ? text("请先由来源负责人批准精确变更，再分别签署成果。", "The source owner must approve the exact change before deliverable owners sign.")
        : approvalLabel(value.approval_set?.status);
    } catch (error) {
      if (generation !== refreshGeneration || eventId !== selectedEvent) return;
      if (error?.status === 409 && error?.code === "WORKSPACE_PREVIEW_REQUIRED") {
        node("deliverable-set-notice").textContent = text("本提案尚未预演。请先查看变更影响，形成候选后再审阅成果。", "This proposal has no preview yet. Preview this change before reviewing its candidate deliverables.")
          + (profileReady ? text(" 上方保留刚核实的正式版本。", " The verified committed versions above remain available.") : "");
        return;
      }
      clearSensitive();
      node("deliverable-set-current-notice").textContent = safeFailure(error);
    }
  }

  async function refreshProfile() {
    const context = contextGeneration;
    const generation = ++profileGeneration;
    labels();
    profileReady = false;
    node("deliverable-set-current").replaceChildren();
    node("deliverable-set-members").replaceChildren();
    node("deliverable-set-current-notice").textContent = text("正在核对当前成果…", "Checking current deliverables…");
    setButtonsDisabled(busy);
    try {
      const profile = await client.json("/api/workspace/deliverable-set");
      if (context !== contextGeneration || generation !== profileGeneration) return;
      panel.hidden = false;
      if (profile.members?.length) {
        const exported = await client.json("/api/workspace/export/quote");
        if (context !== contextGeneration || generation !== profileGeneration) return;
        const members = exported.deliverables;
        if (!Array.isArray(members) || members.length !== profile.members.length
          || !profile.members.every(member => members.some(item => `${item.id}@${item.version}` === member.object_ref
            && item.digest === member.object_digest && item.payload?.deliverable_kind === member.deliverable_kind))) throw new Error("DELIVERABLE_CURRENT_SNAPSHOT_CHANGED");
        for (const item of members) node("deliverable-set-current").append(currentCard(item));
      }
      profileReady = true;
      node("deliverable-set-current-notice").textContent = text("这里显示服务端保存的正式版本；变化候选与成果决定在下方单独审阅。", "These are committed versions saved by the server. Change candidates and deliverable decisions are reviewed separately below.");
      setButtonsDisabled(busy);
      await refreshChange();
    } catch (error) {
      if (context !== contextGeneration || generation !== profileGeneration || error?.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
      clearSensitive();
      panel.hidden = error?.code === "WORKSPACE_DELIVERABLE_SET_PROFILE_NOT_CONFIGURED";
      if (!panel.hidden) node("deliverable-set-current-notice").textContent = safeFailure(error);
      setButtonsDisabled(busy);
    }
  }

  async function downloadSet() {
    if (busy || !profileReady) return;
    const context = contextGeneration;
    busy = true; setButtonsDisabled(true);
    try {
      const value = await client.json("/api/workspace/export/deliverable-set");
      if (context !== contextGeneration) return;
      const href = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], {type: "application/json"}));
      const anchor = document.createElement("a"); anchor.href = href; anchor.download = "orgrebase-deliverable-set.json";
      document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(href);
      node("deliverable-set-current-notice").textContent = text("已下载同一服务端快照的报价、折扣说明及集合回执。", "Downloaded Quote, Memo and set receipts from one server snapshot.");
    } catch (error) {
      if (context !== contextGeneration || error?.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
      clearSensitive(); node("deliverable-set-current-notice").textContent = safeFailure(error);
    } finally { busy = false; setButtonsDisabled(false); }
  }

  node("deliverable-set-refresh").addEventListener("click", refreshProfile);
  node("deliverable-set-download").addEventListener("click", downloadSet);
  window.addEventListener("orgrebase:changeselection", () => setTimeout(refreshChange, 0));
  window.addEventListener("orgrebase:workspacechange", () => { clearSensitive(); refreshProfile(); });
  window.addEventListener("orgrebase:sessionchange", () => { clearSensitive(); refreshProfile(); });
  window.addEventListener("orgrebase:sessionended", () => { clearSensitive(); panel.hidden = true; });
  window.addEventListener("orgrebase:languagechange", () => { labels(); if (!panel.hidden) refreshChange(); });
  refreshProfile();
})();
