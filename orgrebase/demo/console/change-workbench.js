(() => {
  "use strict";
  const client = window.OrgRebaseClient;
  const mount = document.getElementById("task-changes-flow");
  if (!mount || !client) return;
  const root = document.createElement("section");
  root.className = "change-workbench";
  root.id = "change-workbench";
  root.setAttribute("aria-labelledby", "change-workbench-title");
  root.innerHTML = `
    <header class="change-workbench-header"><div><h2 id="change-workbench-title"></h2><p id="change-workbench-intro"></p></div><button class="button button-secondary" type="button" id="change-refresh"></button></header>
    <p id="change-notice" class="change-workbench-notice" role="status" aria-live="polite" hidden></p>
    <details class="change-editor" id="change-editor"><summary id="change-editor-summary"></summary>
      <label><span id="change-field-label"></span><select id="change-field" required></select></label>
      <dl class="change-form-context" id="change-source-context" hidden><div><dt id="change-current-label"></dt><dd id="change-current-value"></dd></div><div><dt id="change-owner-label"></dt><dd id="change-owner-value"></dd></div><div><dt id="change-base-label"></dt><dd id="change-base-value"></dd></div></dl>
      <p id="change-edit-blocked" role="status" hidden></p><form id="change-proposal-form">
        <label><span id="change-operation-label"></span><select id="change-operation" required></select></label>
        <div class="change-form-grid"><div class="change-value-group"><span id="change-value-label"></span><span id="change-value-mount"></span></div><label class="wide"><span id="change-source-label"></span><input id="change-source" required maxlength="1000" autocomplete="off" /></label></div>
        <label><span id="change-reason-label"></span><textarea id="change-proposal-reason" maxlength="1000" rows="3"></textarea></label><p id="change-revision-note" hidden></p><p id="change-editor-boundary"></p>
        <div class="change-workbench-actions"><button id="change-submit" class="button button-primary" type="submit"></button><button id="change-cancel" class="button button-secondary" type="button"></button></div>
      </form>
    </details>
    <div class="change-workbench-layout"><section aria-labelledby="change-list-title"><h3 id="change-list-title"></h3><ul class="change-event-list" id="change-event-list"></ul><p id="change-list-empty"></p><button id="change-load-more" class="button button-secondary" type="button" hidden></button></section><section class="change-detail" id="change-detail" aria-labelledby="change-detail-title"><h3 id="change-detail-title" tabindex="-1"></h3><div id="change-detail-body"></div></section></div>`;
  mount.prepend(root);
  const byId = (id) => document.getElementById(id);
  const authorityOverview = byId("authority-ladder");
  if (authorityOverview) root.firstElementChild.after(authorityOverview);
  const tr = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const valueText = (value) => typeof value === "string" ? value : value == null ? "—" : JSON.stringify(value);
  const labels = {
    launch_date: ["上线日期", "Launch date"], currency: ["报价币种", "Currency"], product_plan: ["产品方案", "Product plan"],
    pricing_policy: ["报价计算规则", "Pricing policy"], quote_basket: ["报价明细", "Quote basket"],
  };
  const domainLabels = {
    product: ["产品", "Product"], finance: ["财务", "Finance"], legal: ["法务", "Legal"],
    gtm: ["市场与商业化", "Go-to-market"],
  };
  const statusLabels = {
    RECEIVED: ["等待预演", "Awaiting preview"], PREVIEWED: ["等待负责人审阅", "Awaiting review"], APPROVED: ["已批准，待应用", "Approved, awaiting apply"],
    GROUP_REVIEW: ["在来源恢复组中审阅", "Review in source recovery group"], GROUP_APPLIED: ["已共同恢复", "Restored together"],
    APPLIED: ["已应用", "Applied"], REJECTED: ["已拒绝", "Rejected"], STALE: ["依据已变化，需重新提案", "Base changed; propose again"],
    RECOVERY_REQUIRED: ["已生效，待恢复结果记录", "Applied; result recovery required"],
    EVIDENCE_REQUIRED: ["待补证", "Awaiting evidence"],
    EXPIRED: ["审批依据已失效", "Review basis invalid"], SCHEDULED: ["尚未到生效时间", "Scheduled"], UNKNOWN: ["结果未知，需核查", "Unknown; reconcile first"],
  };
  let options = null;
  let state = null;
  let events = [];
  let nextCursor = null;
  let selectedId = null;
  let detail = null;
  let busy = false;
  let commandSequence = 0;
  let loadSequence = 0;
  let selectionSequence = 0;
  let selectionPending = false;
  let draft = null;
  let draftEdited = false;
  let activeReviewMs = 0;
  let reviewStarted = null;
  let acknowledgementDigest = null;
  let refreshPromise = null;
  let reviewTimer = null;
  let renderedDetailKey = null;
  let observedRunId = null;
  let readFailure = false;
  let recoveryDraft = {};
  let recoveryDraftKey = null;
  let recoveryOperation = null;
  const sessionIdentity = session => session ? JSON.stringify([session.mode,
    session.principal?.issuer, session.principal?.subject,
    session.principal?.tenant_id, session.principal?.actor_id]) : null;
  let draftSessionIdentity = sessionIdentity(client.session());

  function name(slot) { return labels[slot] ? tr(...labels[slot]) : slot; }
  function status(value) { return statusLabels[value] ? tr(...statusLabels[value]) : tr("状态待核查", "Status needs verification"); }
  function showNotice(message, tone = "error") {
    byId("change-notice").textContent = message;
    byId("change-notice").dataset.tone = tone;
    byId("change-notice").hidden = !message;
  }
  function fail(error) {
    const code = error.code || error.message || "UNAVAILABLE";
    const message = error.status === 401 ? tr("请重新登录；草稿仍保留，操作不会自动重发。", "Sign in again. Your draft is retained; no operation is replayed.")
      : /^CHANGE_RECOVERY_/.test(code) ? code === "CHANGE_RECOVERY_CONTEXT_CHANGED"
        ? tr("变更依据已更新。请刷新并核对新的任务与证据，再发起补证。", "The change context has been updated. Refresh and review the tasks and evidence before requesting a new round.")
        : ["CHANGE_RECOVERY_EVIDENCE_REQUIRED", "CHANGE_RECOVERY_EVIDENCE_CHANGED", "CHANGE_RECOVERY_EVIDENCE_SCOPE"].includes(code)
          ? tr("补证内容与本轮要求不一致。请核对指定依据及其当前版本。", "The evidence does not match this round's requirements. Check the requested references and current versions.")
          : code === "CHANGE_RECOVERY_EXECUTOR_NOT_ADMITTED"
            ? tr("该执行者当前未获准处理此任务，请刷新后选择可用执行者。", "This executor is not admitted for the task. Refresh and choose an available executor.")
            : tr("补证记录或可用操作已变化，请核对下方当前状态。", "The evidence record or available actions changed. Review the current state below.")
      : error.status === 403 ? tr("当前账号没有该操作权限，或会话校验已变化。请刷新登录状态后核对。", "This account cannot perform the action, or session checks changed. Refresh your session and review.")
        : detail?.recovery?.state === "FAILED"
          ? detail.recovery.execution_uncertain
            ? tr("本轮执行回执尚未确认，请先核查原任务记录，当前不能重新委派。", "This attempt's execution receipt is unconfirmed. Check the original task record; it cannot be delegated again yet.")
            : tr("本轮补证执行未完成。请负责人核对失败记录，需要继续时重新发起补证。", "This evidence attempt did not complete. The reviewer should inspect the failure and request a new evidence round if needed.")
        : /WORKSPACE_ADVISORY_IN_PROGRESS/.test(code) ? tr("领域建议正在生成，请稍后刷新查看，无需再次提交。", "Domain advice is being generated. Refresh later to view it; no resubmission is needed.")
          : /WORKSPACE_ADVISORY_(RESULT_UNKNOWN|ATTEMPT_FAILED|INPUT_CHANGED_REQUIRE_NEW_EVENT)/.test(code) ? tr("本次候选未能完成或结果未确认，系统不会自动重复调用。请由负责人拒绝本提案，再修订为新提案。", "This candidate attempt failed or its result is unconfirmed. No automatic retry will occur. Ask the owner to reject this proposal, then revise it as a new proposal.")
            : /OPENAI_CREDENTIALS_MISSING/.test(code) ? tr("模型接入尚未配置。请联系管理员完成配置后重试。", "The model connection is not configured. Ask an administrator to configure it, then retry.")
              : code === "WORKSPACE_RUNTIME_REPLAN_REQUIRED" ? tr("执行代码或模型配置已变化，原预演不能继续用于审批。请刷新后修订为新提案，重新预演并审批。", "Execution code or model configuration changed; the old preview cannot be approved. Refresh, revise as a new proposal, then preview and approve again.")
              : /STALE|CONFLICT|BASE_|EXPIRED|REJECTED/.test(code) ? tr("依据或提案状态已变化。草稿已保留；刷新后核对当前值，再提交新的提案。", "The base or proposal changed. Your draft is retained. Refresh, review the current value, then submit a new proposal.")
                : /REVIEW_GATE_NOT_READY/.test(code) ? tr("审阅等待时间尚未结束。请核对影响范围后再次确认。", "The review interval has not elapsed. Review the effects, then confirm again.")
                  : tr("请求未确认，请刷新核对结果。系统不会自动重复提交。", "The request was not confirmed. Refresh to check the result; no automatic resubmission occurs.");
    showNotice(`${message} (${code})`);
  }
  function setBusy(value) {
    busy = value;
    if (value) pauseReview(); else resumeReview();
    root.setAttribute("aria-busy", String(value));
    root.querySelectorAll("button").forEach((button) => { button.disabled = value || button.dataset.unavailable === "true"; });
    updateReviewButton();
  }
  function invalidateReadModel({ clearDraft = false, retireCommands = clearDraft } = {}) {
    ++loadSequence; ++selectionSequence;
    if (retireCommands) { ++commandSequence; busy = false; }
    selectionPending = false; refreshPromise = null;
    options = null; detail = null; events = []; nextCursor = null; state = null;
    pauseReview(); activeReviewMs = 0; acknowledgementDigest = null;
    if (clearDraft) {
      recoveryDraft = {}; recoveryDraftKey = null; recoveryOperation = null; draft = null; draftEdited = false; selectedId = null; byId("change-proposal-form").reset();
    }
    renderEditor(); renderList(); renderDetail();
    setBusy(busy);
  }
  function field() { return options && options.fields.find((item) => item.slot_id === byId("change-field").value); }
  function rememberFocus(container, fallback = "change-detail-title") {
    const active = document.activeElement;
    if (!active?.id || !container.contains(active)) return () => {};
    const { id, selectionStart, selectionEnd } = active;
    return () => {
      const replacement = byId(id);
      if (!replacement || !container.contains(replacement) || replacement.disabled) { byId(fallback).focus(); return; }
      replacement.focus();
      if (typeof selectionStart === "number" && typeof replacement.selectionStart === "number") replacement.setSelectionRange?.(selectionStart, selectionEnd);
    };
  }
  function rememberDetail(body) {
    const values = new Map();
    for (const id of ["change-detail-reason", "change-delegate-member", "change-delegate-duration", "change-delegate-reason", "change-revoke_delegation-reason", "change-escalate-reason"]) {
      const input = byId(id);
      if (input && body.contains(input)) values.set(id, input.value);
    }
    const expanded = ["change-authority", "change-audit", "change-lineage", "change-pricing-preview-lines", "change-pricing-result-lines", "change-recovery-context"].filter(id => {
      const section = byId(id); return section && body.contains(section) && section.open;
    });
    const restoreFocus = rememberFocus(body);
    return () => {
      for (const [id, value] of values) {
        const input = byId(id);
        if (!input || !body.contains(input)) continue;
        input.value = input.tagName !== "SELECT" || Array.from(input.children).some(option => option.value === value) ? value : "";
      }
      for (const id of expanded) { const section = byId(id); if (section && body.contains(section)) section.open = true; }
      restoreFocus();
    };
  }
  function newDraft() {
    const selected = field();
    draftEdited = false;
    if (!selected || !selected.allowed_operations.length) { draft = null; renderEditor(); return; }
    draft = { event_id: null, slot_id: selected.slot_id, base_version: selected.current.version,
      base_digest: selected.current.digest,
      value: selected.value_kind === "pricing_policy" ? JSON.parse(JSON.stringify(selected.current.value))
        : selected.value_kind === "json" ? JSON.stringify(selected.current.value, null, 2) : "",
      source_ref: "", reason: null, operation: selected.allowed_operations[0], revises_event_id: null };
    renderEditor();
  }
  function basisPointsText(value) {
    return Number.isSafeInteger(value) && value >= 0
      ? `${Math.floor(value / 100)}.${String(value % 100).padStart(2, "0")}` : "";
  }
  function businessValue(slot, value) {
    if (slot === "pricing_policy" && value && typeof value === "object") {
      return tr("折扣", "Discount") + ` ${basisPointsText(value.discount_bps)}% · `
        + `${value.tax_label || tr("税率", "Tax")} ${basisPointsText(value.tax_bps)}%`;
    }
    if (slot === "quote_basket" && Array.isArray(value?.items)) {
      return tr(`${value.items.length} 项报价明细`, `${value.items.length} basket lines`) + ` · ${value.currency || "—"}`;
    }
    return valueText(value);
  }
  function percentBasisPoints(value) {
    if (!/^(?:0|[1-9]\d{0,2})(?:\.\d{1,2})?$/.test(value)) throw new Error("PRICING_PERCENT_INVALID");
    const [whole, fraction = ""] = value.split(".");
    const points = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
    if (points > 10000) throw new Error("PRICING_PERCENT_INVALID");
    return points;
  }
  function editDraft() {
    if (!draft) return;
    draftEdited = true;
    if (field()?.value_kind === "pricing_policy") {
      draft.pricing_input = { discount: byId("change-value")?.value || "", tax: byId("change-tax-rate")?.value || "",
        label: byId("change-tax-label")?.value || "" };
    } else draft.value = byId("change-value")?.value || "";
    draft.source_ref = byId("change-source").value.trim();
    draft.reason = byId("change-proposal-reason").value.trim() || null;
    draft.operation = byId("change-operation").value;
    draft.event_id = null;
  }
  function renderEditor() {
    const restoreFocus = rememberFocus(byId("change-value-mount"), "change-field");
    const selected = field();
    const allowed = Boolean(selected && draft && selected.allowed_operations.includes(draft.operation));
    byId("change-submit").dataset.unavailable = String(!allowed);
    byId("change-submit").disabled = busy || !allowed;
    byId("change-edit-blocked").hidden = allowed;
    if (!allowed) {
      byId("change-edit-blocked").textContent = selected?.blocked_reason === "SOURCE_VALIDITY_EXPIRED"
        ? tr("来源有效期已结束。须重新核验来源资格后才能提出变化。", "The source validity period ended. Requalify the source before proposing a change.")
        : selected?.blocked_reason === "PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE"
          ? tr("当前报价币种须与商品明细一致，暂不支持单独修改。请保持现有币种；跨币种报价需要同时核定明细价格与币种。", "The quote currency must match its line items and cannot be changed independently. Keep the current currency; a currency change requires jointly verified item prices and currency.")
        : selected?.allowed_operations.includes("READMIT") && draft?.operation === "UPDATE"
          ? tr("来源状态已变化，不能继续按原方式更新。草稿已保留；请核对来源并明确选择重新准入。", "The source state changed and an update is no longer allowed. Your draft is retained; review the source and explicitly select readmission.")
        : tr("当前身份或来源状态不允许提出变化。可刷新状态或联系对应负责人。", "Your identity or the source state does not allow a proposal. Refresh or contact the responsible owner.");
    }
    byId("change-proposal-form").hidden = !selected || !draft;
    byId("change-source-context").hidden = !selected;
    byId("change-current-value").textContent = selected ? businessValue(selected.slot_id, selected.current.value) : "";
    byId("change-owner-value").textContent = selected ? selected.owner_id : "";
    byId("change-base-value").textContent = selected ? `${selected.current.version} · ${selected.current.state}` : "";
    if (!selected || !draft) return;
    const operation = byId("change-operation");
    operation.replaceChildren();
    for (const value of selected.allowed_operations) {
      const option = document.createElement("option"); option.value = value;
      option.textContent = value === "READMIT" ? tr("重新确认失效依据", "Readmit invalidated source") : tr("更新业务值", "Update value"); operation.append(option);
    }
    if (!allowed) {
      const unavailable = document.createElement("option"); unavailable.value = draft.operation;
      unavailable.textContent = (draft.operation === "READMIT" ? tr("重新确认失效依据", "Readmit invalidated source") : tr("更新业务值", "Update value"))
        + tr("（当前不可用）", " (currently unavailable)");
      unavailable.disabled = true; operation.append(unavailable);
    }
    operation.value = draft.operation;
    const input = document.createElement(selected.value_kind === "enum" ? "select" : selected.value_kind === "json" ? "textarea" : "input");
    input.id = "change-value"; input.required = true;
    input.setAttribute("aria-labelledby", "change-value-label");
    if (selected.value_kind === "pricing_policy") {
      const policy = draft.value || {}, entries = draft.pricing_input || {
        discount: basisPointsText(policy.discount_bps), tax: basisPointsText(policy.tax_bps), label: policy.tax_label || "",
      };
      const group = document.createElement("div"); group.className = "change-pricing-inputs";
      for (const [id, title, value] of [
        ["change-value", tr("折扣率（%）", "Discount (%)"), entries.discount],
        ["change-tax-rate", tr("税率（%）", "Tax (%)"), entries.tax],
        ["change-tax-label", tr("税费口径说明", "Tax basis label"), entries.label],
      ]) {
        const label = document.createElement("label"), caption = document.createElement("span");
        const control = id === "change-value" ? input : document.createElement("input");
        caption.textContent = title; control.id = id; control.type = "text"; control.required = true;
        control.value = value; control.maxLength = id === "change-tax-label" ? 160 : 6;
        if (id !== "change-tax-label") { control.inputMode = "decimal"; control.pattern = "(?:0|[1-9][0-9]{0,2})(?:[.][0-9]{1,2})?"; }
        control.addEventListener("input", editDraft); label.append(caption, control); group.append(label);
      }
      input.removeAttribute("aria-labelledby");
      byId("change-value-mount").replaceChildren(group);
    } else if (selected.value_kind === "enum") {
      const empty = document.createElement("option"); empty.value = ""; empty.textContent = tr("选择新值", "Choose a value"); input.append(empty);
      for (const choice of selected.choices || []) {
        const option = document.createElement("option"); option.value = typeof choice === "string" ? choice : choice.value;
        option.textContent = typeof choice === "string" ? choice : choice.label || choice.value; input.append(option);
      }
    } else if (selected.value_kind === "json") {
      input.rows = 8; input.maxLength = 64000; input.spellcheck = false;
    } else { input.type = selected.value_kind === "date" ? "date" : "text"; input.maxLength = 500; input.autocomplete = "off"; }
    if (selected.value_kind !== "pricing_policy") {
      input.value = draft.value;
      input.addEventListener("input", editDraft);
      byId("change-value-mount").replaceChildren(input);
    }
    byId("change-source").value = draft.source_ref;
    byId("change-proposal-reason").value = draft.reason || "";
    byId("change-revision-note").hidden = !draft.revises_event_id;
    byId("change-revision-note").textContent = tr("修订自：", "Revises: ") + (draft.revises_event_id || "");
    const stale = draft.base_digest !== selected.current.digest || draft.base_version !== selected.current.version;
    if (stale) showNotice(tr("编辑期间依据已更新。已保留输入；请点“使用当前依据”重新核对后提交。", "The base changed while editing. Your input is retained. Use the current base, then review and submit."));
    byId("change-cancel").textContent = stale ? tr("使用当前依据", "Use current base") : tr("清空草稿", "Clear draft");
    byId("change-submit").dataset.unavailable = String(stale || !allowed);
    byId("change-submit").disabled = busy || stale || !allowed;
    restoreFocus();
  }
  function renderOptions() {
    const selector = byId("change-field");
    const previous = draft && draft.slot_id || selector.value;
    selector.replaceChildren();
    for (const item of options.fields) {
      const option = document.createElement("option"); option.value = item.slot_id;
      const label = document.documentElement.lang === "en" ? name(item.slot_id) : item.label || name(item.slot_id);
      const domain = Object.hasOwn(domainLabels, item.domain_id) ? tr(...domainLabels[item.domain_id]) : item.domain_id;
      option.textContent = (domain ? `${domain} · ` : "") + label
        + (item.allowed_operations.length ? "" : tr("（当前不可提案）", " (proposal unavailable)"));
      selector.append(option);
    }
    if (options.fields.some((item) => item.slot_id === previous)) selector.value = previous;
    if (!draft || draft.slot_id !== selector.value || (!draftEdited && !draft.revises_event_id)) newDraft(); else renderEditor();
  }
  function renderList() {
    byId("change-event-list").replaceChildren();
    for (const item of events) {
      const event = item.event;
      const row = document.createElement("li");
      const button = document.createElement("button"); button.type = "button";
      button.setAttribute("aria-current", String(event.event_id === selectedId));
      const heading = document.createElement("strong"); heading.textContent = name(event.slot_id);
      const stateLabel = document.createElement("span"); stateLabel.textContent = status(item.status);
      stateLabel.className = "change-status";
      stateLabel.dataset.status = Object.hasOwn(statusLabels, item.status) ? item.status : "UNKNOWN";
      const value = document.createElement("small"); value.textContent = businessValue(event.slot_id, event.proposal.payload.canonical_value);
      const responsibility = document.createElement("small");
      const authority = item.responsibility;
      const owner = authority?.owner_id || event.owner_id;
      const facts = [tr("原负责人：", "Original owner: ") + valueText(owner)];
      if (["APPROVED", "APPLIED", "REJECTED", "RECOVERY_REQUIRED"].includes(item.status)) {
        if (authority?.decision_actor_id) facts.push(tr("决定人：", "Decision by: ") + authority.decision_actor_id);
      } else if (["RECEIVED", "PREVIEWED", "EVIDENCE_REQUIRED", "EXPIRED", "STALE"].includes(item.status)) {
        if (authority?.blocked_reason === "CHANGE_OWNER_REPLAN_REQUIRED") {
          facts.push(tr("职责已变更，需按当前负责人重新提案", "Responsibility changed; submit a new proposal for the current owner"));
        } else if (["DELEGATION_EXPIRED", "DELEGATION_REVOKED"].includes(authority?.blocked_reason)) {
          facts.push(authority.blocked_reason === "DELEGATION_EXPIRED"
            ? tr("委派已过期；原负责人权限仍按当前规则核验", "Delegation expired; the original owner's authority is checked against current rules")
            : tr("委派已撤销；原负责人权限仍按当前规则核验", "Delegation revoked; the original owner's authority is checked against current rules"));
        } else if (authority?.blocked_reason) facts.push(tr("当前授权需核查", "Current authority needs verification"));
        else if (["RECEIVED", "PREVIEWED", "EVIDENCE_REQUIRED"].includes(item.status) && authority?.delegate_id) facts.push(tr("有效受托审阅人：", "Active delegated reviewer: ") + authority.delegate_id);
        if (["EXPIRED", "STALE"].includes(item.status) && authority?.blocked_reason !== "CHANGE_OWNER_REPLAN_REQUIRED") {
          facts.push(tr("查看失效依据，重新预演或提案", "Inspect the invalid basis before a new preview or proposal"));
        }
      }
      if (item.status === "GROUP_REVIEW") facts.push(tr("按恢复组共同审阅", "Review through the recovery group"));
      responsibility.textContent = facts.join(" · ");
      button.append(heading, stateLabel, value, responsibility); button.addEventListener("click", () => select(event.event_id, { focus: true })); row.append(button); byId("change-event-list").append(row);
    }
    byId("change-list-empty").hidden = events.length > 0;
    byId("change-load-more").hidden = nextCursor == null;
  }
  function pauseReview() {
    if (reviewStarted !== null) activeReviewMs += Math.max(0, performance.now() - reviewStarted);
    reviewStarted = null;
  }
  function resumeReview() {
    pauseReview();
    if (!busy && detail && detail.preview && document.visibilityState === "visible" && document.hasFocus()
      && byId("change-detail").contains(document.activeElement)) reviewStarted = performance.now();
  }
  async function observation(action, outcome, reason, reviewed = detail, elapsed = activeReviewMs) {
    pauseReview();
    if (!reviewed?.preview?.preview_digest) return;
    try {
      await client.json(`/api/workspace/changes/${encodeURIComponent(reviewed.event.event_id)}/review-observation`, {
        method: "POST", body: { observation_id: `review:${crypto.randomUUID()}`, preview_digest: reviewed.preview.preview_digest, active_ms: Math.min(86400000, Math.round(elapsed)), action, outcome, reason_code: reason || null },
      });
    } catch (_) { /* Telemetry never changes command success or grants authority. */ }
    resumeReview();
  }
  function addFact(dl, label, value, wide = false) {
    const group = document.createElement("div"); if (wide) group.className = "wide";
    const term = document.createElement("dt"); term.textContent = label;
    const definition = document.createElement("dd"); definition.textContent = valueText(value);
    group.append(term, definition); dl.append(group);
  }
  function renderDomainAdvice(body, bundle) {
    const handoffs = bundle?.advisory?.handoffs;
    const candidates = (Array.isArray(handoffs) ? handoffs : [])
      .map(handoff => handoff?.payload?.model_advisory?.receipt?.value)
      .filter(candidate => typeof candidate?.explanation === "string" && candidate.explanation.trim());
    if (!candidates.length) return;
    const heading = document.createElement("h3"); heading.textContent = tr("领域建议", "Domain advice");
    const note = document.createElement("p");
    note.textContent = tr("以下建议供审阅参考；实际批准仍以本次完整影响集合为准。", "These suggestions support your review. Approval remains bound to the complete effect set above.");
    const list = document.createElement("dl"); list.id = "change-domain-advice";
    const domains = { product: ["产品", "Product"], legal: ["法务", "Legal"], finance: ["财务", "Finance"], gtm: ["商务", "Commercial"] };
    for (const candidate of candidates) {
      const domain = Object.hasOwn(domains, candidate.domain_id) ? tr(...domains[candidate.domain_id]) : tr("业务领域", "Business domain");
      const objects = (Array.isArray(candidate.object_ids) ? candidate.object_ids : [])
        .filter(objectId => typeof objectId === "string")
        .map(objectId => typeof window.businessChangeObjectLabel === "function" ? window.businessChangeObjectLabel(objectId) : objectId);
      addFact(list, objects.length ? `${domain} · ${objects.join(tr("、", ", "))}` : domain, candidate.explanation, true);
    }
    body.append(heading, note, list);
  }
  function updateReviewButton() {
    if (reviewTimer !== null) { clearTimeout(reviewTimer); reviewTimer = null; }
    const button = byId("change-approve-exact");
    if (!button || !detail || !detail.allowed_actions.includes("APPROVE")) return;
    const preview = detail.preview;
    const gate = preview && preview.review_gate && preview.review_gate.gate;
    const verified = gate && gate.preview_digest === preview.preview_digest
      && Number.isFinite(gate.not_before_epoch_ms) && gate.not_before_epoch_ms >= 0;
    const remaining = verified ? Math.max(0, gate.not_before_epoch_ms - Date.now()) : null;
    button.disabled = busy || button.dataset.unavailable === "true" || remaining === null || remaining > 0;
    button.textContent = remaining === null ? tr("审阅状态待核验", "Review status unverified")
      : remaining > 0 ? tr(`请继续审阅（${Math.ceil(remaining / 1000)} 秒）`, `Continue reviewing (${Math.ceil(remaining / 1000)}s)`)
        : tr("确认并批准", "Approve exact proposal");
    if (remaining > 0) reviewTimer = setTimeout(updateReviewButton, Math.min(1000, remaining + 5));
  }
  function boundNativeExecution(native) {
    if (!native || native.schema_version !== "orgrebase.change-agentteams-execution.v1"
      || native.candidate_only !== true || native.target_writes !== 0 || !native.binding
      || native.binding.run_id !== detail?.execution_run_id
      || !native.binding.change_set_ref?.startsWith(`changeset:workspace-${detail.event.event_id}@`)
      || !Array.isArray(native.tasks) || !Array.isArray(native.actions)) return null;
    const bundle = detail.preview?.bundle;
    if (bundle && (!matching(native.binding.change_set_digest, bundle.change_set?.digest)
      || !matching(native.binding.preview_digest, bundle.preview?.digest)
      || !matching(native.binding.plan_digest, bundle.advisory?.orchestration_plan?.digest)
      || !matching(native.binding.nonce, bundle.run_envelope?.nonce)
      || !matching(native.binding.change_set_ref, `${bundle.change_set?.id}@${bundle.change_set?.revision}`))) return null;
    return native;
  }
  const taskPhases = [
    ["delegate", "delegate_task", "委派", "Delegation"], ["ack", "ack_task", "接单", "Acknowledgement"],
    ["submit", "submit_task", "提交", "Submission"], ["check", "check_task", "AT 检查", "AT check"],
    ["accept", "accept_task_result", "接收", "Acceptance"],
  ];
  const taskRecords = value => Array.isArray(value) ? value : [];
  function oneTaskRecord(records, predicate) {
    const found = taskRecords(records).filter(predicate);
    return found.length === 1 ? found[0] : null;
  }
  function sameTaskRefs(left, right) {
    return Array.isArray(left) && Array.isArray(right) && left.every(item => typeof item === "string")
      && right.every(item => typeof item === "string") && new Set(left).size === left.length
      && new Set(right).size === right.length && left.length === right.length && left.every(item => right.includes(item));
  }
  function changeTaskEvidence(native, task) {
    const bundle = detail.preview?.bundle, advisory = bundle?.advisory, plan = advisory?.orchestration_plan;
    const full = boundNativeExecution(advisory?.native_execution);
    if (!full || !matching(native.receipt_digest || native.digest, full.digest)
      || !matching(native.project_id, full.project_id) || !matching(plan?.digest, native.binding.plan_digest)
      || !matching(plan?.change_set_digest, native.binding.change_set_digest)
      || !matching(plan?.preview_digest, native.binding.preview_digest)
      || !matching(plan?.revision_lock_digest, native.binding.revision_lock_digest)) return null;
    const mapped = oneTaskRecord(full.tasks, item => item.task_id === task.task_id);
    if (!mapped || !matching(task.logical_task_id, mapped.logical_task_id)
      || !matching(task.delegation_digest, mapped.delegation_digest)
      || !matching(task.actor_id, mapped.actor_id) || task.kind !== mapped.kind
      || !sameTaskRefs(task.depends_on, mapped.depends_on)) return null;
    const ingestion = advisory.ingestion_receipt, coordination = advisory.coordination_receipt;
    const receiptBound = ingestion?.target_writes === 0
      && matching(ingestion.run_id, native.binding.run_id) && matching(ingestion.nonce, native.binding.nonce)
      && matching(ingestion.change_set_digest, native.binding.change_set_digest)
      && matching(ingestion.preview_digest, native.binding.preview_digest)
      && matching(ingestion.orchestration_plan_digest, plan.digest)
      && Array.isArray(ingestion.rejected_candidate_digests) && ingestion.rejected_candidate_digests.length === 0;
    if (task.kind === "DETERMINISTIC_CONTRACT_REVIEW") {
      const review = oneTaskRecord(full.results, item => item.review_kind === task.kind);
      if (!receiptBound || !review || task.actor_id !== "independent-contract-reviewer"
        || task.logical_task_id !== `${plan.id}:contract-review`
        || review.candidate_only !== true || review.target_writes !== 0 || review.verdict !== "PASS"
        || !sameTaskRefs(review.reviewed_handoff_digests, taskRecords(advisory.handoffs).map(item => item.digest))
        || !matching(review.reviewed_bundle_digest, native.review?.reviewed_bundle_digest)
        || native.review?.verdict !== review.verdict || native.review?.review_kind !== review.review_kind
        || !sameTaskRefs(native.review?.reviewed_handoff_digests, review.reviewed_handoff_digests)
        || coordination?.status !== "PASS" || !matching(ingestion.live_receipt_digest, coordination.digest)
        || !matching(coordination.workflow_run_id, native.binding.run_id)
        || !matching(coordination.run_nonce, native.binding.nonce)
        || !matching(coordination.orchestration_plan_digest, plan.digest)
        || !sameTaskRefs(coordination.handoff_digests, review.reviewed_handoff_digests)
        || !native.tasks.filter(item => item.kind === "CANDIDATE").every(item => changeTaskEvidence(native, item))) return null;
      return { review, plan };
    }
    const delegation = oneTaskRecord(plan.tasks, item => item.id === task.logical_task_id);
    const handoff = oneTaskRecord(advisory.handoffs, item => item.task_id === task.logical_task_id);
    const run = oneTaskRecord(advisory.agent_runs, item => item.task_id === task.logical_task_id);
    const result = oneTaskRecord(full.results, item => item.logical_task_id === task.logical_task_id);
    const decision = handoff && oneTaskRecord(ingestion?.decisions, item => item.artifact_ref === handoff.id);
    if (task.kind !== "CANDIDATE" || !delegation || !handoff || !run || !result || !receiptBound
      || delegation.candidate_only !== true || !matching(delegation.digest, task.delegation_digest, handoff.delegation_task_digest, result.delegation_digest)
      || !matching(delegation.agent_name, task.actor_id, handoff.from_agent, run.agent_name)
      || !matching(handoff.workflow_run_id, run.workflow_run_id, native.binding.run_id)
      || !matching(handoff.run_nonce, run.run_nonce, native.binding.nonce)
      || !matching(handoff.change_set_id, native.binding.change_set_ref)
      || !matching(handoff.orchestration_plan_digest, plan.digest)
      || !sameTaskRefs(handoff.input_refs, delegation.input_refs)
      || !sameTaskRefs(handoff.payload?.input_refs, delegation.input_refs)
      || !sameTaskRefs(delegation.depends_on, task.depends_on.map(id => oneTaskRecord(full.tasks, item => item.task_id === id)?.logical_task_id))
      || !matching(handoff.payload?.preview_digest, native.binding.preview_digest)
      || handoff.candidate_only !== true || handoff.payload?.candidate_only !== true || handoff.payload?.target_writes !== 0
      || result.candidate_only !== true || result.target_writes !== 0
      || !matching(result.handoff_digest, handoff.digest) || !matching(result.agent_run_digest, run.digest)
      || !decision || decision.decision !== "ADVISORY_ACCEPTED" || !matching(decision.artifact_digest, handoff.digest)
      || !matching(decision.producer_worker, task.actor_id) || !Array.isArray(decision.admitted_effects) || decision.admitted_effects.length
      || !taskRecords(delegation.allowed_output_kinds).includes(handoff.payload.kind)
      || typeof handoff.to_agent !== "string" || !handoff.to_agent) return null;
    return { delegation, handoff, run, plan };
  }
  function changeTaskActions(native, task) {
    const full = boundNativeExecution(detail.preview?.bundle?.advisory?.native_execution);
    return taskPhases.map(([suffix, action]) => {
      const receipt = oneTaskRecord(native.actions, item => item.key === `${task.task_id}:${suffix}` && item.action === action);
      const original = full && oneTaskRecord(full.actions, item => item.key === receipt?.key && item.action === action);
      return receipt?.ok === true && typeof receipt.digest === "string"
        && taskRecords(task.action_receipt_digests).includes(receipt.digest)
        && (!full || (original?.ok === true && matching(original.digest, receipt.digest))) ? receipt : null;
    });
  }
  function renderChangeTasks(section, execution) {
    const native = boundNativeExecution(execution);
    if (!native) return;
    const container = document.createElement("section"); container.id = "change-agentteams-tasks";
    const previousNative = detail?.recovery?.preserved_context?.previous_native_receipt_digest;
    const historical = previousNative && matching(previousNative, native.receipt_digest || native.digest);
    const title = document.createElement("h4"); title.textContent = historical
      ? tr("补证前的 AgentTeams 任务记录", "AgentTeams task records before the evidence request")
      : tr("本次变更的 AgentTeams 任务", "AgentTeams tasks for this change"); container.append(title);
    if (native.actions.some(action => action.key === "project:plan" && action.ok === true)) {
      const tasks = document.createElement("ol");
      for (const task of native.tasks) {
        const row = document.createElement("li"); row.dataset.nativeTask = task.task_id;
        const card = document.createElement("details"); card.dataset.changeTask = task.task_id;
        const summary = document.createElement("summary"), facts = document.createElement("dl");
        const evidence = changeTaskEvidence(native, task), actions = changeTaskActions(native, task);
        const last = actions.filter(Boolean).at(-1), phase = taskPhases.find(item => item[1] === last?.action);
        const actor = task.kind === "DETERMINISTIC_CONTRACT_REVIEW" ? tr("独立契约复核", "Independent contract review") : task.actor_id;
        summary.textContent = `${actor} · ${phase ? tr(phase[2], phase[3]) : tr("等待委派", "Awaiting delegation")}`;
        summary.title = task.task_id; card.append(summary);
        const missing = tr("未记录或无法关联本任务", "Not recorded or not bound to this task");
        if (!evidence) {
          const notice = document.createElement("p"); notice.textContent = missing; card.append(notice);
        } else if (evidence.review) {
          addFact(facts, tr("职责", "Responsibility"), tr("确定性检查候选绑定、权限与零写边界", "Deterministically check candidate bindings, authority and zero-write boundaries"));
          addFact(facts, tr("输入", "Input"), tr(`${evidence.review.reviewed_handoff_digests.length} 份精确绑定的候选交接`, `${evidence.review.reviewed_handoff_digests.length} exactly bound candidate handoffs`));
          addFact(facts, tr("复核种类", "Review kind"), "DETERMINISTIC_CONTRACT_REVIEW");
          addFact(facts, tr("复核结果", "Review result"), evidence.review.verdict);
          addFact(facts, tr("效应边界", "Effect boundary"), tr("仅候选 · 已记录目标写入 0；不是云模型调用或人工批准", "Candidate-only · recorded target writes 0; not a cloud-model invocation or human approval"));
        } else {
          const {delegation, handoff, run} = evidence;
          const kinds = {TaskGraph:["候选任务图", "Candidate task graph"], SemanticExplanation:["规则变化解释", "Rule-change explanation"], ImpactCandidate:["影响候选", "Impact candidate"]};
          const kind = kinds[handoff.payload.kind];
          addFact(facts, tr("职责 / 产出类型", "Responsibility / output type"), kind ? tr(...kind) : tr("已绑定候选类型", "Bound candidate type"));
          addFact(facts, tr("输入范围（准入投影）", "Input scope (admitted projections)"), taskRecords(delegation.context_scope).filter(item => typeof item === "string"));
          addFact(facts, tr("输入依据引用", "Input references"), taskRecords(delegation.input_refs).filter(item => typeof item === "string"), true);
          const allowedObjects = new Set(taskRecords(detail.preview.bundle.change_set.deltas).map(item => item.object_id));
          const objects = handoff.payload.change_object_ids;
          addFact(facts, tr("关联变更对象", "Related changed objects"), Array.isArray(objects) && objects.every(item => typeof item === "string" && allowedObjects.has(item)) ? objects : missing);
          addFact(facts, tr("交给谁", "Recipient"), handoff.to_agent);
          addFact(facts, tr("执行记录", "Execution record"), run.status);
          addFact(facts, tr("效应边界", "Effect boundary"), tr("仅候选 · 已记录目标写入 0；候选已准入不等于人工批准", "Candidate-only · recorded target writes 0; admission is not human approval"));
          addFact(facts, tr("Tool / Skill", "Tool / Skill"), tr("本卡仅展示任务和候选绑定；能力要求或版本引用不是实际调用回执", "This card shows task and candidate bindings; capability requirements or version references are not invocation receipts"));
        }
        if (evidence) {
          addFact(facts, tr("依赖的 AT 任务", "AT task dependencies"), taskRecords(task.depends_on));
          addFact(facts, tr("逻辑任务", "Logical task"), task.logical_task_id, true);
          addFact(facts, tr("委派摘要", "Delegation digest"), task.delegation_digest, true);
          card.append(facts);
        }
        const receipts = document.createElement("ol"); receipts.dataset.changeTaskReceipts = task.task_id;
        taskPhases.forEach((item, index) => {
          const line = document.createElement("li"); line.dataset.taskPhase = item[1];
          line.textContent = `${tr(item[2], item[3])} · ${actions[index] ? tr("已记录", "Recorded") : tr("未记录", "Not recorded")}`;
          if (actions[index]) { const digest = document.createElement("code"); digest.textContent = actions[index].digest; line.append(digest); }
          receipts.append(line);
        });
        card.append(receipts); row.append(card); tasks.append(row);
      }
      container.append(tasks);
    } else {
      const pending = document.createElement("p"); pending.textContent = tr("正在建立本次变更的任务。", "Creating tasks for this change."); container.append(pending);
    }
    const scope = document.createElement("p");
    scope.textContent = tr("这些是固定版本 AT 在本地受控环境中的实际任务。契约复核校验绑定与权限，不代替业务判断；人工批准和正式生效在下方单独确认。", "These are actual tasks executed by the pinned AT in a controlled-local environment. Contract review checks binding and authority, not business judgment; human approval and application remain separate below.");
    const identity = document.createElement("details"), summary = document.createElement("summary"), value = document.createElement("code");
    summary.textContent = tr("原生执行身份", "Native execution identity"); value.textContent = `${native.project_id} · ${native.receipt_digest || native.digest || ""}`;
    identity.append(summary, value); container.append(scope, identity); section.append(container);
  }
  function renderAdvisoryAttempt(body, attempt, id = "change-advisory-attempt") {
    if (!attempt) return;
    const states = {IN_PROGRESS:["候选生成中", "Candidate generation in progress"], RESULT_UNKNOWN:["本次生成结果未知", "This generation result is unknown"], FAILED:["本次生成失败", "This generation failed"], COMPLETE:["本次候选验证已完成", "This candidate was verified"]};
    const section = document.createElement("section"); section.id = id;
    const heading = document.createElement("h3"); heading.textContent = tr("候选生成记录", "Candidate generation record"); section.append(heading);
    const facts = document.createElement("dl");
    addFact(facts, tr("本次尝试", "This attempt"), states[attempt.state] ? tr(...states[attempt.state]) : tr("状态未知", "Unknown state"));
    const reservation = attempt.cost_reservation;
    const usd = value => Number.isSafeInteger(value) && value >= 0 ? `USD ${(value / 1000000).toFixed(6)}` : tr("未知", "Unknown");
    if (reservation?.scope === "ONE_PREVIEW_ATTEMPT" && reservation.currency === "USD") {
      addFact(facts, tr("本轮费用预留上限", "Cost reserved for this attempt"), usd(reservation.reserved_microusd));
      addFact(facts, tr("单轮配置上限", "Configured per-attempt limit"), usd(reservation.limit_microusd));
    }
    addFact(facts, tr("整轮用量", "Usage for the whole attempt"), attempt.usage_status === "OBSERVED"
      ? tr("回执已记录用量，仍以供应商账单为准", "Receipt usage observed; the provider bill remains authoritative")
      : tr("未知，不能按零用量计算", "Unknown; do not count as zero usage"));
    if (attempt.public_error_code) addFact(facts, tr("当前失败原因", "Current failure reason"), attempt.public_error_code, true);
    section.append(facts);
    const boundary = document.createElement("p"); boundary.textContent = tr("费用预留是单次尝试的上限，不是实际账单。候选验证完成也不代表提案已获准、已批准或已应用。", "Reserved cost is a per-attempt ceiling, not an actual bill. Candidate verification does not mean the proposal was admitted, approved, or applied."); section.append(boundary);
    if (["FAILED", "RESULT_UNKNOWN"].includes(attempt.state)) {
      const recovery = document.createElement("p");
      const current = id === "change-advisory-attempt" && attempt === detail?.advisory_attempt ? detail : null;
      recovery.textContent = current && ["REJECTED", "STALE", "EXPIRED"].includes(current.status)
        ? current.allowed_actions?.includes("REVISE")
          ? tr("本次尝试已保留。请补齐资料，再用下方“修订为新提案”继续。", "This attempt is preserved. Complete the evidence, then use “Revise as new proposal” below.")
          : tr("本次尝试已保留。请有提案权限的成员补齐资料，再修订为新提案。", "This attempt is preserved. A member with proposal permission can complete the evidence and revise it as a new proposal.")
        : current?.recovery && current.recovery.state !== "NONE"
          ? tr("请按下方补证记录继续；本次任务和上下文保留，批准仍需重新确认。", "Continue using the evidence record below. The task and context are preserved; approval requires fresh confirmation.")
        : current ? tr(`下一步由 ${current.active_owner_id || current.event.owner_id} 核对失败原因并记录拒绝；随后可修订为新提案。刷新不会再次调用模型。`, `Next, ${current.active_owner_id || current.event.owner_id} reviews the failure reason and records a rejection; the proposal can then be revised. Refresh does not call the model again.`)
          : tr("刷新可核对持久记录，不会自动再次调用模型。需要新候选时，请负责人按现有流程拒绝此提案，再修订为新提案。", "Refresh reads persisted records without calling the model again. For a new candidate, the owner can use the existing rejection and revision workflow.");
      section.append(recovery);
    }
    if (attempt.receipt_summaries?.length) {
      const receipts = document.createElement("details"), summary = document.createElement("summary"), pre = document.createElement("pre"); summary.textContent = tr("派发与用量回执", "Dispatch and usage receipts");
      pre.textContent = JSON.stringify(attempt.receipt_summaries.map(receipt => ({dispatch_state:receipt.dispatch_state, provider_request_id:receipt.provider_request_id, input_tokens:receipt.input_tokens, output_tokens:receipt.output_tokens})), null, 2);
      receipts.append(summary, pre); section.append(receipts);
    }
    if (id === "change-advisory-attempt") {
      renderChangeTasks(section, detail?.preview?.native_execution || attempt.native_execution);
    }
    body.append(section);
  }
  const quoteFields = [
    ["owner", "交付负责人", "Delivery owner"], ["deliverable_kind", "交付类型", "Deliverable kind"],
    ["customer_id", "客户", "Customer"], ["product_plan", "产品方案", "Product plan"],
    ["launch_date", "上线日期", "Launch date"], ["data_residency", "数据驻留", "Data residency"],
    ["notice_required", "通知要求", "Notice required"], ["price_band", "价格档位", "Price band"],
    ["currency", "币种", "Currency"], ["partner_terms_code", "合作条款", "Partner terms"],
  ];
  const objectRef = object => object?.id && object?.version ? `${object.id}@${object.version}` : null;
  const matching = (...values) => typeof values[0] === "string" && values[0].length > 0 && values.every(value => value === values[0]);
  const resultSnapshots = new WeakMap();

  function authorityProgress() {
    if (!detail || detail.event.event_id !== selectedId || detail.execution_run_id !== options?.execution_run_id) return null;
    const recoveryPending = ["NEEDS_EVIDENCE", "RESUMING", "FAILED"].includes(detail.recovery?.state);
    const preview = detail.preview, bundle = preview?.bundle, advisory = bundle?.advisory;
    const plan = advisory?.orchestration_plan, ingestion = advisory?.ingestion_receipt, coordination = advisory?.coordination_receipt;
    const previewBound = Boolean(bundle && matching(detail.event.event_id, preview.kind)
      && matching(detail.execution_run_id, bundle.run_envelope?.run_id)
      && matching(detail.event.proposal?.id, bundle.change_spec?.object_id)
      && matching(detail.event.base_version, bundle.change_spec?.base_version)
      && matching(detail.event.proposal?.version, bundle.change_spec?.proposed_version)
      && matching(preview.preview_digest, bundle.preview?.digest, plan?.preview_digest)
      && matching(bundle.change_set?.digest, plan?.change_set_digest));
    const admitted = Boolean(!recoveryPending && previewBound && ingestion?.target_writes === 0
      && matching(ingestion.run_id, bundle.run_envelope.run_id)
      && matching(ingestion.nonce, bundle.run_envelope.nonce)
      && matching(ingestion.change_set_digest, bundle.change_set.digest)
      && matching(ingestion.preview_digest, preview.preview_digest)
      && matching(ingestion.orchestration_plan_digest, plan.digest)
      && Array.isArray(ingestion.decisions) && ingestion.decisions.length > 0
      && ingestion.decisions.every(decision => decision.decision === "ADVISORY_ACCEPTED")
      && Array.isArray(ingestion.rejected_candidate_digests) && ingestion.rejected_candidate_digests.length === 0);
    const native = boundNativeExecution(preview?.native_execution || detail.advisory_attempt?.native_execution);
    const completed = Boolean(!recoveryPending && native?.status === "COMPLETED" && native.native_agentteams_observed === true
      && native.actions.some(action => action.key === "project:complete" && action.ok === true));
    const reviewed = Boolean(admitted && coordination?.status === "PASS"
      && matching(ingestion.live_receipt_digest, coordination.digest)
      && matching(coordination.workflow_run_id, bundle.run_envelope.run_id)
      && matching(coordination.run_nonce, bundle.run_envelope.nonce)
      && matching(coordination.orchestration_plan_digest, plan.digest)
      && (!(preview?.native_execution || advisory?.native_execution) || completed && native.tasks.some(task => task.kind === "DETERMINISTIC_CONTRACT_REVIEW"
        && native.actions.some(action => action.key?.startsWith(`${task.task_id}:`) && action.action === "accept_task_result" && action.ok === true))));
    const envelope = detail.approval, approval = envelope?.approval, binding = envelope?.binding;
    const gate = preview?.review_gate?.gate;
    const reviewObserved = envelope?.approval_review_evidence?.review_wait_satisfied === true
      || gate?.review_duration_ms === 0 && matching(gate.preview_digest, preview.preview_digest)
        && matching(gate.workflow_run_id, detail.execution_run_id) && matching(gate.run_nonce, bundle?.run_envelope?.nonce);
    const approved = Boolean(previewBound && approval && binding
      && matching(detail.event.event_id, envelope.kind, binding.change_kind)
      && matching(detail.execution_run_id, binding.workflow_run_id)
      && matching(bundle.run_envelope.nonce, binding.run_nonce)
      && matching(preview.preview_digest, binding.preview_digest, approval.preview_digest)
      && matching(bundle.change_set.digest, binding.change_set_digest, approval.change_set_digest)
      && matching(envelope.approval_digest, approval.digest, binding.approval_digest)
      && reviewObserved);
    const effect = appliedEvidence(), failed = ["FAILED", "RESULT_UNKNOWN"].includes(detail.advisory_attempt?.state);
    const rejected = detail.status === "REJECTED" && detail.rejection;
    const invalidated = ["EXPIRED", "STALE"].includes(detail.status);
    const steps = [
      {key:"proposal", label:tr("业务提出", "Business proposal"), status:"complete",
        text:tr("提案已登记；登记操作不修改现行规则", "Proposal registered; registration makes no rule changes")},
      {key:"agentteams", label:tr("AgentTeams 协作", "AgentTeams coordination"),
        status:recoveryPending ? detail.recovery.state === "RESUMING" ? "current" : "blocked" : completed ? "complete" : failed ? "blocked" : native ? "current" : "unobserved",
        text:recoveryPending ? detail.recovery.state === "RESUMING" ? tr("补证任务重新委派中", "Evidence task is being delegated again") : tr("等待补齐依据并继续任务", "Awaiting evidence and task continuation") : completed ? tr(`${native.tasks.length} 项任务已完成`, `${native.tasks.length} tasks completed`)
          : native ? tr("读取本次任务的实际推进记录", "Reading this change's actual task progress")
            : tr("尚无本次原生 AT 记录", "No native AT record for this change")},
      {key:"candidate", label:tr("候选准入", "Candidate admission"), status:admitted ? "complete" : failed || recoveryPending ? "blocked" : "waiting",
        text:admitted ? tr("精确绑定的建议已接收；业务写入为 0", "Exact-bound advice received; zero business writes")
          : tr("等待候选与影响范围校验", "Awaiting candidate and impact checks")},
      {key:"review", label:tr("独立契约复核", "Independent contract review"), status:reviewed ? "complete" : "waiting",
        text:reviewed ? tr("绑定与权限检查通过；业务判断仍由负责人作出", "Binding and authority checks passed; the owner retains business judgment")
          : tr("尚无本次完整契约复核证据", "No complete contract-review evidence for this change")},
      {key:"human", label:tr("人工决定", "Human decision"), status:rejected ? "blocked" : approved ? "complete" : invalidated ? "blocked" : "waiting",
        text:recoveryPending ? tr(`待补证后重新审阅 · ${detail.active_owner_id || detail.event.owner_id}`, `Review again after evidence · ${detail.active_owner_id || detail.event.owner_id}`)
          : rejected ? tr(`已拒绝 · ${detail.rejection.actor_id}`, `Rejected · ${detail.rejection.actor_id}`)
          : approved ? tr(`批准已记录 · ${approval.actor_id}`, `Approval recorded · ${approval.actor_id}`)
            : invalidated ? tr("当前提案不能批准，请查看阻断原因后修订", "This proposal cannot be approved. Review the blocker and revise")
              : tr(`待负责人确认 · ${detail.active_owner_id || detail.event.owner_id}`, `Awaiting owner · ${detail.active_owner_id || detail.event.owner_id}`)},
      {key:"effect", label:tr("正式生效", "Committed effect"), status:effect ? "complete" : detail.status === "RECOVERY_REQUIRED" ? "current" : rejected || invalidated ? "blocked" : "waiting",
        text:effect ? `${effect.binding.predecessor_ref} → ${objectRef(effect.quote)}`
          : detail.status === "RECOVERY_REQUIRED" ? tr("已提交，待恢复结果记录", "Committed; result record recovery required")
            : invalidated ? tr("当前提案不可生效，请按阻断原因修订", "This proposal cannot take effect. Revise after addressing the blocker")
              : tr("尚无本次已核对的生效结果", "No verified committed result for this change")},
    ];
    return {event_id:detail.event.event_id, event_digest:detail.event.digest, execution_run_id:detail.execution_run_id, steps};
  }

  function usesLogicalBusinessTime() {
    return state?.execution?.oac_agentteams_lineage?.context_freshness_basis === "LOGICAL_EVENT_TIME"
      || state?.enterprise_data_lineage?.oac?.context_freshness_basis === "LOGICAL_EVENT_TIME";
  }

  function businessTimeLabel(zh, en) {
    return tr(zh, en) + (usesLogicalBusinessTime()
      ? tr(" · 业务逻辑时间（受控运行）", " · business logical time (controlled run)") : "");
  }

  function appliedEvidence() {
    const outcome = detail?.outcome?.outcome, preview = detail?.preview, bundle = preview?.bundle;
    const approval = detail?.approval?.approval, binding = detail?.approval?.binding;
    const receipt = outcome?.rebase_receipt, commit = outcome?.workspace_rebase_receipt, quote = outcome?.quote;
    if (!outcome || !bundle || !approval || !binding || !receipt || !commit || !quote) return null;
    if (detail.status !== "APPLIED" || receipt.status !== "COMPLETED" || commit.status !== "COMPLETED"
      || !matching(detail.event.event_id, detail.outcome.kind, outcome.kind, binding.change_kind)
      || !matching(detail.execution_run_id, bundle.run_envelope?.run_id, binding.workflow_run_id, receipt.workflow_run_id)
      || !matching(bundle.run_envelope?.nonce, binding.run_nonce, receipt.run_nonce)
      || !matching(bundle.change_spec?.object_id, detail.event.proposal?.id)
      || !matching(bundle.change_spec?.base_version, detail.event.base_version)
      || !matching(bundle.change_spec?.proposed_version, detail.event.proposal?.version)
      || !matching(bundle.change_spec?.digest, outcome.change_spec?.digest)
      || !matching(bundle.change_set?.digest, outcome.change_set?.digest, binding.change_set_digest, approval.change_set_digest)
      || !matching(receipt.change_set_ref, `${bundle.change_set.id}@${bundle.change_set.revision}`)
      || !matching(receipt.preview_ref, bundle.preview?.id)
      || !matching(preview.preview_digest, bundle.preview?.digest, outcome.preview_digest, binding.preview_digest, approval.preview_digest)
      || !matching(approval.digest, detail.approval.approval_digest, outcome.approval?.digest, outcome.approval_digest, receipt.approval_digest)
      || !matching(receipt.id, commit.base_rebase_receipt_ref) || !matching(receipt.digest, commit.base_rebase_receipt_digest)
      || !matching(quote.payload?.rebased_from, binding.predecessor_ref)
      || !Array.isArray(commit.successor_object_refs) || commit.successor_object_refs.length !== 1
      || !matching(objectRef(quote), commit.successor_object_refs[0])
      || !matching(objectRef(outcome.graph_pointer), commit.graph_pointer_ref)) return null;
    return { outcome, bundle, approval, binding, receipt, commit, quote };
  }

  function renderContextUsage(container, receipt, bundle) {
    const section = document.createElement("section"); section.id = "change-context-usage";
    const heading = document.createElement("h4"); heading.textContent = tr("本次记录的上下文使用", "Context use recorded for this change"); section.append(heading);
    const contexts = receipt.context_manifests;
    const note = document.createElement("p"); section.append(note); container.append(section);
    if (!Array.isArray(contexts) || !contexts.length) {
      note.textContent = tr("本次未记录上下文清单，不能据此推断使用或排除的数量。", "No context manifest was recorded; usage and exclusion counts cannot be inferred.");
      section.dataset.status = "NOT_RECORDED"; return;
    }
    const targets = new Set(Array.isArray(receipt.transitions) ? receipt.transitions.map(item => item?.object_id) : []);
    const seen = new Set();
    const valid = contexts.every(context => {
      const ref = objectRef(context);
      if (!ref || seen.has(ref) || typeof context.actor_id !== "string" || !context.actor_id.trim()
        || !/^sha256:[0-9a-f]{64}$/.test(context.digest || "")
        || !targets.has(context.target_object_id)
        || !matching(context.purpose, bundle.change_set?.purpose)
        || !matching(context.graph_revision, receipt.revision_lock?.graph_revision)
        || !matching(context.policy_revision, receipt.revision_lock?.policy_revision)
        || !Array.isArray(context.included) || !Array.isArray(context.excluded)
        || ![...context.included, ...context.excluded].every(item => item && typeof item.disposition === "string")) return false;
      seen.add(ref); return true;
    });
    if (!valid) {
      section.dataset.status = "INVALID";
      note.textContent = tr("上下文记录缺失、重复或与本次生效记录不一致，暂不展示汇总。", "Context records are incomplete, duplicated or do not match this change; the summary is unavailable."); return;
    }
    section.dataset.status = "RECORDED";
    note.textContent = tr("按每份清单分别计数，不合并为不同资料总量。引用的是已准入投影，不代表读取了原始文件；这里只解释当时记录，不重新判定历史权限。", "Counts are per manifest, not unique sources across manifests. References are admitted projections, not evidence of raw-file access. This explains recorded decisions; it does not reevaluate historical permissions.");
    const reasons = {
      OTHER_AUTHORITY_DOMAIN: ["属于其他领域权限范围", "Outside this domain's authority"],
      MINIMAL_DISCLOSURE_DERIVATION_ONLY: ["只允许使用派生信息", "Derived information only"],
      FORBIDDEN_OUTPUT_FIELD: ["不允许出现在交付物中", "Forbidden in the deliverable"],
      FORBIDDEN_FOR_QUOTE: ["不允许出现在报价中", "Forbidden in the quote"],
      SENSITIVITY_CEILING_EXCEEDED: ["超出敏感度范围", "Above the sensitivity ceiling"],
      NOT_REQUIRED_FOR_TASK: ["当前任务不需要", "Not required by this task"],
      PURPOSE_NOT_ALLOWED: ["用途未获准", "Purpose not permitted"],
      GOVERNANCE_STATE_NOT_USABLE: ["资料状态不可用于本次工作", "Source state not usable"],
      UNKNOWN_PRINCIPAL: ["执行主体未确认", "Actor not established"],
    };
    for (const context of contexts) {
      const facts = document.createElement("dl"); facts.className = "change-context-record";
      addFact(facts, tr("执行主体", "Executing actor"), context.actor_id);
      addFact(facts, tr("清单引用", "Manifest reference"), objectRef(context), true);
      const premises = context.included.filter(item => item.disposition === "INCLUDED_AS_PREMISE");
      const derived = context.included.filter(item => item.disposition === "DERIVED");
      const excluded = context.excluded.filter(item => item.disposition === "EXCLUDED");
      const other = context.included.length + context.excluded.length - premises.length - derived.length - excluded.length;
      addFact(facts, tr("引用前提记录", "Premise records"), premises.length);
      addFact(facts, tr("派生信息记录", "Derived records"), derived.length);
      if (other) addFact(facts, tr("未识别的处置类型", "Unrecognized dispositions"), other);
      const labels = [...premises, ...derived].map(item => typeof item.label === "string" && item.label.trim()
        ? item.label : tr("未记录名称", "Name not recorded"));
      if (labels.length) addFact(facts, tr("使用的投影名称", "Used projection labels"), labels.join(" · "), true);
      const counts = new Map();
      for (const item of excluded) {
        const label = Object.hasOwn(reasons, item.reason_code) ? tr(...reasons[item.reason_code]) : tr("其他已记录原因", "Other recorded reason");
        counts.set(label, (counts.get(label) || 0) + 1);
      }
      const exclusionNotes = [...counts].map(([reason, count]) => `${reason} · ${count}`);
      const unknownExclusions = context.excluded.length - excluded.length;
      if (unknownExclusions) exclusionNotes.push(tr(`排除列表中有 ${unknownExclusions} 项处置未识别，不能确认其含义`,
        `${unknownExclusions} unrecognized dispositions in the exclusion list; their meaning is unknown`));
      addFact(facts, tr("排除记录", "Exclusion records"), context.excluded.length
        ? exclusionNotes.join("；") : tr("本次未记录排除项", "No exclusions recorded for this manifest"), true);
      section.append(facts);
    }
  }

  function renderPricingComparison(section, before, after, { preview = false, linesExpanded = false } = {}) {
    const previous = before.payload?.pricing, current = after.payload?.pricing;
    if (!previous || !current) return;
    const wrapper = document.createElement("div"); wrapper.className = "change-pricing-summary";
    const table = document.createElement("table"); table.className = "change-quote-comparison"; table.id = preview ? "change-pricing-preview-table" : "change-pricing-comparison";
    const caption = document.createElement("caption");
    const sameBasket = matching(previous.basket_digest, current.basket_digest);
    caption.textContent = preview
      ? tr("金额预演，尚未生效", "Amount preview — not yet applied") : sameBasket
      ? tr("同一报价明细，规则生效前后的金额", "Same basket: amounts before and after the policy takes effect")
      : tr("生效前后金额：报价明细同时发生了变化", "Amounts before and after: the basket also changed");
    table.append(caption);
    const head = document.createElement("thead"), headers = document.createElement("tr");
    for (const title of [tr("金额项", "Amount"), tr(`变更前 ${before.version}`, `Before ${before.version}`),
      preview ? after.version : tr(`变更后 ${after.version}`, `After ${after.version}`)]) {
      const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = title; headers.append(cell);
    }
    head.append(headers); table.append(head);
    const body = document.createElement("tbody");
    for (const [key, zh, en] of [["subtotal", "商品小计", "Subtotal"], ["discount_amount", "折扣金额", "Discount"],
      ["net_amount", "折后净额", "Net amount"], ["tax_amount", "税额", "Tax"], ["total", "报价总额", "Total"]]) {
      const row = document.createElement("tr"); row.dataset.pricingField = key;
      const title = document.createElement("th"); title.scope = "row"; title.textContent = tr(zh, en); row.append(title);
      for (const value of [previous, current]) {
        const cell = document.createElement("td"); cell.textContent = typeof value[key] === "string" ? `${value[key]} ${value.currency}` : "—"; row.append(cell);
      }
      row.dataset.result = previous[key] === current[key] && previous.currency === current.currency ? "PRESERVED" : "CHANGED";
      body.append(row);
    }
    table.append(body); wrapper.append(table);
    const note = document.createElement("p"); note.className = "change-result-note";
    note.textContent = (preview
      ? tr("金额来自当前提案锁定的预演；批准并应用前，现行报价不变。税费口径：", "Amounts come from this proposal's locked preview; the current quote stays unchanged until approval and apply. Tax basis: ")
      : tr("金额直接来自批准绑定的前驱与生效版本，页面不重新计算。税费口径：", "Amounts come directly from the approval-bound predecessor and committed successor; this page does not recalculate. Tax basis: "))
      + `${previous.tax_label || "—"} → ${current.tax_label || "—"}`;
    if (preview) note.textContent += sameBasket
      ? tr("；报价明细未变。", "; the basket is unchanged.") : tr("；报价明细同时发生变化。", "; the basket also changes.");
    wrapper.append(note);
    renderQuoteLineChanges(wrapper, before, after, { preview, expanded: linesExpanded });
    section.append(wrapper);
  }

  function renderQuoteLineChanges(section, before, after, { preview, expanded }) {
    const previous = before.payload.pricing, current = after.payload.pricing;
    if (!Array.isArray(previous.lines) || !Array.isArray(current.lines)) return;
    const fields = [["sku", "SKU", "SKU"], ["description", "商品", "Item"],
      ["quantity", "数量", "Quantity"], ["unit_price", "单价", "Unit price"], ["line_total", "行金额", "Line amount"]];
    const prior = new Map(previous.lines.map(line => [line.line_id, line]));
    const next = new Map(current.lines.map(line => [line.line_id, line]));
    const changes = [];
    for (const id of new Set([...prior.keys(), ...next.keys()])) {
      const oldLine = prior.get(id), newLine = next.get(id);
      if (oldLine && newLine && previous.currency === current.currency
        && fields.every(([key]) => oldLine[key] === newLine[key])) continue;
      changes.push({ id, oldLine, newLine, kind: !oldLine ? "ADDED" : !newLine ? "REMOVED" : "MODIFIED" });
    }
    if (!changes.length) return;
    const details = document.createElement("details");
    details.id = preview ? "change-pricing-preview-lines" : "change-pricing-result-lines";
    details.open = expanded;
    const labels = { ADDED: ["新增", "Added"], REMOVED: ["移除", "Removed"], MODIFIED: ["修改", "Modified"] };
    const summary = document.createElement("summary");
    summary.id = `${details.id}-summary`;
    const counts = Object.entries(labels).map(([kind, label]) => {
      const count = changes.filter(item => item.kind === kind).length;
      return count ? `${tr(...label)} ${count}` : null;
    }).filter(Boolean).join(" · ");
    summary.textContent = (preview ? tr("拟议商品明细变化：", "Proposed line changes: ") : tr("已生效商品明细变化：", "Applied line changes: ")) + counts;
    details.append(summary);
    const table = document.createElement("table"); table.className = "change-quote-comparison";
    const caption = document.createElement("caption");
    caption.textContent = tr("只列变化明细；数量、单价和行金额均来自已核对的服务端版本，总额不变也会列出。", "Changed lines only; quantities, unit prices and line amounts come from the matched server versions, including changes with an unchanged total.");
    table.append(caption);
    const head = document.createElement("thead"), headers = document.createElement("tr");
    for (const title of [tr("明细与变化", "Line and change"), tr(`变更前 ${before.version}`, `Before ${before.version}`),
      preview ? after.version : tr(`变更后 ${after.version}`, `After ${after.version}`)]) {
      const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = title; headers.append(cell);
    }
    head.append(headers); table.append(head);
    const body = document.createElement("tbody");
    for (const change of changes) {
      const row = document.createElement("tr"); row.dataset.lineId = change.id;
      row.dataset.quoteLineChange = change.kind; row.dataset.result = "CHANGED";
      const title = document.createElement("th"); title.scope = "row";
      title.textContent = `${change.id} · ${tr(...labels[change.kind])}`; row.append(title);
      for (const [line, currency] of [[change.oldLine, previous.currency], [change.newLine, current.currency]]) {
        const cell = document.createElement("td");
        if (!line) cell.textContent = tr("此版本无该明细", "No line in this version");
        else for (const [key, zh, en] of fields) {
          const fact = document.createElement("div");
          const value = line[key] == null ? "—" : String(line[key]);
          fact.textContent = `${tr(zh, en)}: ${value}${["unit_price", "line_total"].includes(key) ? ` ${currency}` : ""}`;
          cell.append(fact);
        }
        row.append(cell);
      }
      body.append(row);
    }
    table.append(body); details.append(table); section.append(details);
  }

  function equalJsonValue(left, right) {
    if (left === null || right === null) return left === right;
    if (typeof left !== typeof right) return false;
    if (typeof left === "number") return Number.isFinite(left) && Number.isFinite(right) && left === right;
    if (["string", "boolean"].includes(typeof left)) return left === right;
    if (Array.isArray(left) || Array.isArray(right)) {
      return Array.isArray(left) && Array.isArray(right) && left.length === right.length
        && left.every((value, index) => equalJsonValue(value, right[index]));
    }
    if (Object.prototype.toString.call(left) !== "[object Object]"
      || Object.prototype.toString.call(right) !== "[object Object]") return false;
    const leftKeys = Object.keys(left).sort(), rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length && leftKeys.every((key, index) => key === rightKeys[index]
      && equalJsonValue(left[key], right[key]));
  }

  function renderPricingPreview(section) {
    if (!section) return;
    const linesExpanded = section.querySelector?.("#change-pricing-preview-lines")?.open === true;
    const focusedId = section.contains(document.activeElement) ? document.activeElement.id : null;
    section.replaceChildren();
    const preview = detail?.preview, comparison = preview?.pricing_comparison, bundle = preview?.bundle;
    section.hidden = !comparison || !["PREVIEWED", "APPROVED"].includes(detail?.status);
    if (section.hidden) return;
    const candidates = state?.enterprise_data_lineage?.quotes?.versions;
    const predecessors = Array.isArray(candidates) ? candidates.filter(item => item.ref === comparison.predecessor_ref && item.digest === comparison.predecessor_digest) : [];
    const predecessor = predecessors.length === 1 ? predecessors[0] : null;
    const bound = predecessor?.payload?.pricing && comparison.before && comparison.after
      && matching(detail.event.event_id, selectedId, preview.kind)
      && matching(detail.execution_run_id, options?.execution_run_id, state?.execution?.run_id, state?.enterprise_data_lineage?.run_id)
      && matching(comparison.proposal_digest, detail.event.proposal.digest)
      && matching(comparison.snapshot_digest, bundle?.snapshot_digest, bundle?.change_spec?.expected_snapshot_digest)
      && matching(preview.preview_digest, bundle?.preview?.digest)
      && matching(detail.event.proposal.id, bundle?.change_spec?.object_id)
      && matching(detail.event.proposal.version, bundle?.change_spec?.proposed_version)
      && equalJsonValue(comparison.before, predecessor.payload.pricing);
    if (!bound) {
      const note = document.createElement("p"); note.textContent = tr("金额预演与当前提案或前驱版本的绑定尚未核对，暂不展示金额。请刷新核验。", "The amount preview is not yet matched to this proposal and its predecessor. Refresh to verify before viewing amounts."); section.append(note); return;
    }
    renderPricingComparison(section, predecessor, {version: tr("拟议结果", "Proposed result"), payload: {pricing: comparison.after}}, {preview: true, linesExpanded});
    if (focusedId) section.querySelector?.(`#${focusedId}`)?.focus();
  }

  function renderChangeResult(section) {
    if (!section) return;
    const snapshot = JSON.stringify([state?.execution?.run_id, state?.enterprise_data_lineage?.run_id,
      state?.enterprise_data_lineage?.quotes, state?.task_intake, state?.formation?.digest]);
    if (resultSnapshots.get(section) === snapshot) return;
    resultSnapshots.set(section, snapshot);
    const expanded = byId("change-lineage")?.open === true;
    const linesExpanded = byId("change-pricing-result-lines")?.open === true;
    const restoreFocus = rememberFocus(section);
    section.replaceChildren();
    section.hidden = !detail?.outcome && detail?.status !== "APPLIED";
    if (section.hidden) return;
    const heading = document.createElement("h3"); heading.textContent = tr("本次变更的生效结果", "Effect of this change"); section.append(heading);
    const evidence = appliedEvidence();
    section.dataset.evidenceStatus = evidence ? "BOUND" : "INCOMPLETE";
    if (!evidence) {
      const message = document.createElement("p"); message.textContent = tr("生效记录缺失或绑定不一致，暂不展示已核对的前后对照。请刷新并核查审计记录。", "The effect record is missing or its bindings differ. A verified comparison is unavailable; refresh and inspect the audit records."); section.append(message); restoreFocus(); return;
    }
    const { outcome, bundle, approval, binding, receipt, commit, quote } = evidence;
    const summary = document.createElement("p"); summary.textContent = `${binding.predecessor_ref} → ${objectRef(quote)} · ${commit.committed_at}`; section.append(summary);
    const history = state?.execution?.run_id === detail.execution_run_id && state?.enterprise_data_lineage?.run_id === detail.execution_run_id
      ? state.enterprise_data_lineage.quotes?.versions : null;
    const candidates = Array.isArray(history) ? history.filter(item => item?.ref === binding.predecessor_ref) : [];
    const predecessor = candidates.length === 1 && candidates[0].digest === binding.predecessor_digest ? candidates[0] : null;
    const completeFields = [predecessor, quote].every(item => item?.payload && quoteFields.every(([key]) => Object.hasOwn(item.payload, key)));
    if (completeFields) {
      const table = document.createElement("table"); table.className = "change-quote-comparison"; table.id = "change-quote-comparison";
      const caption = document.createElement("caption"); caption.textContent = tr("同一报价对象，逐字段核对本次变化", "Same quote object: compare this change field by field"); table.append(caption);
      const header = document.createElement("thead"), headerRow = document.createElement("tr");
      for (const title of [tr("字段", "Field"), tr(`变更前 ${predecessor.version}`, `Before ${predecessor.version}`),
        tr(`变更后 ${quote.version}`, `After ${quote.version}`), tr("结果", "Result")]) {
        const cell = document.createElement("th"); cell.setAttribute("scope", "col"); cell.textContent = title; headerRow.append(cell);
      }
      header.append(headerRow); table.append(header);
      const rows = document.createElement("tbody");
      for (const [key, zh, en] of quoteFields) {
        const row = document.createElement("tr"), equal = predecessor.payload[key] === quote.payload[key];
        row.dataset.field = key; row.dataset.result = equal ? "PRESERVED" : "CHANGED";
        const label = document.createElement("th"); label.setAttribute("scope", "row"); label.textContent = tr(zh, en); row.append(label);
        for (const value of [predecessor.payload[key], quote.payload[key], equal ? tr("字段值一致", "Value preserved") : tr("本次变化", "Changed here")]) {
          const cell = document.createElement("td"); cell.textContent = valueText(value); row.append(cell);
        }
        rows.append(row);
      }
      table.append(rows); section.append(table);
      renderPricingComparison(section, predecessor, quote, { linesExpanded });
      const note = document.createElement("p"); note.className = "change-result-note";
      note.textContent = tr("对照来自本次批准绑定的前驱与生效版本；未变业务字段由执行器校验。", "The comparison uses the predecessor bound to this approval and its committed successor; the executor verifies unchanged business fields."); section.append(note);
    } else {
      const missing = document.createElement("p"); missing.textContent = tr("当前历史窗口中没有可核对的完整前驱版本，暂不能逐字段比较。生效结果仍绑定上方版本，不会以最新报价代替。", "The current history window lacks a complete, matching predecessor, so a field comparison is unavailable. The effect remains bound to the versions above; the latest quote is not substituted."); section.append(missing);
    }
    const lineage = document.createElement("details"); lineage.id = "change-lineage"; lineage.open = expanded;
    const title = document.createElement("summary"); title.id = "change-lineage-title"; title.textContent = tr("追溯本次变更的依据与执行", "Trace this change's evidence and execution"); lineage.append(title);
    const facts = document.createElement("dl");
    addFact(facts, tr("来源与变更", "Sources and change"), `${detail.event.event_id}\n${detail.event.proposal.source_refs.join("\n")}\n${outcome.change_set.id}@${outcome.change_set.revision}`, true);
    const intake = state?.task_intake, formation = state?.formation;
    const sourceTaskBound = intake?.schema_version === "orgrebase.workspace-task-intake-run-receipt.v1"
      && intake.status === "FORMATION_COMPLETED" && intake.intake_persisted === true
      && matching(state.execution?.run_id, detail.execution_run_id, intake.run_id)
      && matching(intake.formation_receipt_digest, formation?.digest)
      && matching(intake.quote_ref, formation?.deliverable_ref);
    addFact(facts, tr("源任务登记", "Originating task receipt"), sourceTaskBound
      ? `${intake.actor_id}\n${intake.task_digest}\n${intake.artifact_id}\n${intake.artifact_payload_digest}`
      : tr("未记录与本次运行匹配的任务发起回执", "No originating-task receipt bound to this run"), true);
    const native = boundNativeExecution(detail.preview?.native_execution);
    if (native) addFact(facts, tr("本次 AT 项目与任务", "AT project and tasks for this change"),
      `${native.project_id}\n${native.tasks.map(task => `${task.actor_id} · ${task.task_id}`).join("\n")}\n${native.receipt_digest || native.digest}`, true);
    addFact(facts, tr("预演与影响依据", "Preview and impact evidence"), outcome.preview_digest, true);
    const agents = Array.isArray(receipt.agent_runs) ? receipt.agent_runs : [];
    const boundAgents = agents.filter(agent => matching(agent.workflow_run_id, receipt.workflow_run_id) && matching(agent.run_nonce, receipt.run_nonce));
    addFact(facts, tr("变更执行任务与轨迹", "Change execution tasks and traces"), boundAgents.length
      ? boundAgents.map(agent => `${agent.agent_name} · ${agent.status}\n${agent.task_id}\n${agent.trace_id}\n${agent.evidence_class}`).join("\n\n")
      : tr("没有本次运行的 Agent 轨迹", "No Agent trace bound to this run"), true);
    const skills = [...new Set(boundAgents.flatMap(agent => Array.isArray(agent.skill_versions) ? agent.skill_versions : []))];
    addFact(facts, tr("执行使用的 Skill", "Skills used in execution"), skills.length ? skills.join("\n") : tr("未记录", "Not recorded"), true);
    addFact(facts, tr("资格评测报告引用", "Qualification report reference"), receipt.qualification_report?.id || tr("未记录", "Not recorded"), true);
    const contexts = Array.isArray(receipt.context_manifests) ? receipt.context_manifests : [];
    addFact(facts, tr("上下文引用", "Context references"), contexts.length ? contexts.map(context => `${context.id}@${context.version} · ${context.actor_id}\n${context.digest}`).join("\n\n") : tr("未记录", "Not recorded"), true);
    addFact(facts, businessTimeLabel("批准依据", "Approval evidence"), `${approval.actor_id} · ${approval.approved_at}\n${approval.digest}`, true);
    addFact(facts, businessTimeLabel("生效记录", "Effect receipt"), `${commit.id} · ${commit.committed_at}\n${commit.digest}\n${receipt.evidence_class}`, true);
    addFact(facts, tr("版本锁", "Revision lock"), Object.entries(receipt.revision_lock || {}).map(([key, value]) => `${key}: ${value}`).join("\n"), true);
    lineage.append(facts);
    renderContextUsage(lineage, receipt, bundle);
    const boundary = document.createElement("p"); boundary.textContent = tr("任务、批准和生效记录已关联到本次变更。此处为内部报价结果；外部系统更新需另查执行记录。任务输入和上下文可按引用追溯。", "Tasks, approval and effect records are linked to this change. This is the internal quote result; external updates require their own execution records. Input and context references provide traceability."); lineage.append(boundary);
    section.append(lineage);
    restoreFocus();
  }

  function renderDetail() {
    const body = byId("change-detail-body");
    const key = detail ? JSON.stringify([detail.execution_run_id, detail.event.event_id, detail.event.digest]) : null;
    const restoreDetail = key && key === renderedDetailKey ? rememberDetail(body) : () => {};
    renderedDetailKey = key;
    body.replaceChildren();
    if (!detail) {
      byId("change-detail-title").textContent = tr("选择一项变化", "Select a change");
      window.dispatchEvent(new CustomEvent("orgrebase:changeselection"));
      return;
    }
    const event = detail.event;
    byId("change-detail-title").textContent = `${name(event.slot_id)} · ${status(detail.status)}`;
    const facts = document.createElement("dl");
    const source = options?.fields.find((item) => item.slot_id === event.slot_id);
    const delta = detail.preview?.bundle?.change_set?.deltas?.find((item) => item.object_id === event.proposal.id && item.base_version === event.base_version);
    const priorValue = delta ? delta.base_value : source?.current.digest === event.base_digest ? source.current.value : tr("等待预演确认", "Awaiting exact preview");
    addFact(facts, tr("提案依据值", "Base value"), businessValue(event.slot_id, priorValue));
    addFact(facts, tr("建议的新值", "Proposed value"), businessValue(event.slot_id, event.proposal.payload.canonical_value));
    addFact(facts, tr("原负责人", "Original owner"), event.owner_id);
    if (detail.active_owner_id && detail.active_owner_id !== event.owner_id) addFact(facts, tr("本次委派负责人", "Delegated reviewer"), detail.active_owner_id);
    if (detail.approval?.approval?.actor_id) addFact(facts, tr("实际审批人", "Approved by"), detail.approval.approval.actor_id);
    addFact(facts, tr("依据", "Source"), event.proposal.source_refs.join("\n"), true);
    if (detail.reason) addFact(facts, tr("变更原因", "Reason for change"), detail.reason, true);
    if (detail.approval_expiry) addFact(facts, businessTimeLabel("审批有效期", "Approval validity"), detail.approval_expiry, true);
    if (usesLogicalBusinessTime()) addFact(facts, tr("时间口径", "Time basis"), tr("本页业务批准与生效时间使用受控逻辑时钟；实际API调用与服务端审阅门时间另记真实时间，原始时间值保持不变。", "Business approval and effect timestamps use the controlled logical clock. Actual API calls and server review gates retain separate wall-clock timestamps; original values are unchanged."), true);
    if (detail.status_reason && ["EXPIRED", "STALE", "SCHEDULED"].includes(detail.status)) {
      const reasons = {
        CHANGE_OWNER_REPLAN_REQUIRED: ["负责人或职责绑定已变化，请按当前负责人重新提案。", "The owner or responsibility binding changed. Propose again under the current owner."],
        PROPOSAL_NOT_YET_EFFECTIVE: ["尚未到提案的生效时间。", "The proposal's effective time has not arrived."],
        PROPOSAL_VALIDITY_EXPIRED: ["提案的有效期已结束，请确认新的有效依据后修订。", "The proposal validity period ended. Revise after confirming current evidence."],
        CHANGE_BASE_STATE_CHANGED: ["依据对象状态已变化，请先核查来源，再修订提案。", "The base object's state changed. Check the source before revising."],
        CHANGE_BASE_VERSION_CHANGED: ["依据版本已被更新，请按当前值修订提案。", "The base version was updated. Revise against the current value."],
        READ_DEPENDENCY_CHANGED: ["读取的来源依据已变化，请重新核对后修订。", "The source evidence changed. Review it before revising."],
        READ_DEPENDENCY_UNKNOWN: ["来源依据暂时无法确认，请先补齐可核验的依据。", "The source evidence cannot be confirmed. Supply verifiable evidence first."],
        READ_DEPENDENCY_SOURCE_CONFIG_UNAVAILABLE: ["来源连接配置暂时不可用，请联系管理员恢复后再核查。", "The source connection is unavailable. Ask an administrator to restore it before review."],
        RUNTIME_BINDING_MISSING: ["缺少原预演的运行时绑定，请修订后重新预演。", "The original preview's runtime binding is missing. Revise and preview again."],
        RUNTIME_BINDING_SCOPE_CHANGED: ["预演所属工作区或运行范围已变化，请重新提案。", "The preview's workspace or run scope changed. Create a new proposal."],
        RUNTIME_IMPLEMENTATION_CHANGED: ["执行代码或模型配置已更新，原预演不能继续使用。请修订后重新预演与批准。", "Execution code or model configuration changed. Revise for a new preview and approval."],
        CHANGE_SNAPSHOT_CHANGED: ["组织快照已更新，请按当前上下文重新预演修订提案。", "The organization snapshot changed. Preview a revised proposal against the current context."],
        PREVIEW_VALIDITY_EXPIRED: ["预演的有效期已结束，请修订后重新预演与批准。", "The preview expired. Revise for a new preview and approval."],
        APPROVAL_VALIDITY_EXPIRED: ["批准的有效期已结束，请修订后重新预演与批准。", "The approval expired. Revise for a new preview and approval."],
        APPROVAL_AUTHORITY_CHANGED: ["批准者资格或委派已失效，请核对当前负责人后重新提案。", "The approver's authority or delegation is no longer valid. Confirm the current owner before proposing again."],
      };
      const reason = reasons[detail.status_reason];
      addFact(facts, tr("当前阻断原因", "Current blocker"), reason ? tr(...reason) : tr("当前依据不再允许继续，请核对后修订提案。", "The current evidence does not allow continuation. Review it before revising."));
      facts.children[facts.children.length - 1].title = detail.status_reason;
    }
    if (detail.rejection) {
      addFact(facts, tr("拒绝原因", "Rejection reason"), detail.rejection.reason, true);
      addFact(facts, tr("拒绝记录人", "Decision recorded by"), detail.rejection.actor_id);
    }
    if (detail.revises_event_id) addFact(facts, tr("修订自", "Revises"), detail.revises_event_id, true);
    body.append(facts);
    const pricingPreview = document.createElement("section"); pricingPreview.id = "change-preview-pricing"; body.append(pricingPreview);
    renderPricingPreview(pricingPreview);
    const result = document.createElement("section"); result.id = "change-result"; body.append(result);
    renderChangeResult(result);
    renderAdvisoryAttempt(body, detail.advisory_attempt);
    const preview = detail.preview;
    const bundle = preview && preview.bundle;
    const certificate = bundle && typeof window.exactVmrcBinding === "function" ? window.exactVmrcBinding(bundle) : null;
    if (preview) {
      const heading = document.createElement("h3"); heading.textContent = tr("本次确认覆盖的全部影响", "All effects covered by this review"); body.append(heading);
      const list = document.createElement("ul");
      if (certificate) for (const effect of certificate.effects) {
        const item = document.createElement("li");
        const effectLabels = { REBUILD: ["重建", "Rebuild"], PRESERVE_WITHIN_BOUNDARY: ["在声明范围内保留", "Preserve within boundary"], REQUALIFY: ["重新核验资格", "Requalify"], HOLD_FOR_REVIEW: ["暂停，待核查", "Hold for review"] };
        item.textContent = `${effectLabels[effect.disposition] ? tr(...effectLabels[effect.disposition]) : tr("待核查", "Review required")} · ${typeof window.businessChangeObjectLabel === "function" ? window.businessChangeObjectLabel(effect.target_id) : effect.target_id}`; list.append(item);
      } else { const item = document.createElement("li"); item.textContent = tr("无法核验完整影响集合，批准保持关闭。", "The complete effect set could not be verified. Approval remains closed."); list.append(item); }
      body.append(list);
    }
    renderDomainAdvice(body, bundle);
    const recoveryKey = JSON.stringify([client.session()?.principal?.actor_id, client.workspace?.(),
      detail.execution_run_id, event.event_id, detail.recovery?.context_digest, detail.recovery?.recovery_digest, detail.recovery?.state]);
    if (recoveryDraftKey !== recoveryKey) { recoveryDraft = {}; recoveryOperation = null; recoveryDraftKey = recoveryKey; }
    window.OrgRebaseChangeRecovery?.render(body, detail, {busy, isBusy: () => busy, draft: recoveryDraft,
      onDraft: value => { recoveryDraft = value; }, onCommand: recoverEvidence});
    const actions = Array.isArray(detail.allowed_actions) ? detail.allowed_actions : [];
    const recovering = detail.status === "RECOVERY_REQUIRED";
    if (recovering) {
      const notice = document.createElement("p");
      notice.textContent = tr("业务变更已经提交，结果记录尚未补齐。有执行权限的账号可恢复原记录，无需重新批准或另提变更。", "The business change is committed, but its result record is incomplete. An authorized executor can recover the original record without another approval or proposal.");
      body.append(notice);
    }
    if (actions.includes("APPROVE")) {
      const continuation = document.createElement("p");
      continuation.textContent = tr("批准后，如当前账号有执行权限，系统会自动应用这份提案；否则保留批准，交由有权执行者应用。", "After approval, this proposal is applied automatically if your account has execution permission; otherwise it awaits an authorized executor.");
      body.append(continuation);
      const label = document.createElement("label"); label.className = "change-workbench-actions";
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.id = "change-review-ack"; checkbox.style.width = "auto";
      checkbox.checked = Boolean(certificate && acknowledgementDigest === preview.preview_digest);
      checkbox.disabled = !certificate;
      checkbox.addEventListener("change", () => { acknowledgementDigest = checkbox.checked ? preview.preview_digest : null; renderDetail(); byId("change-review-ack").focus(); });
      label.append(checkbox, document.createTextNode(tr("已核对新值、依据与完整影响集合", "I reviewed the value, source, and complete effect set"))); body.append(label);
    }
    const controls = document.createElement("div"); controls.className = "change-workbench-actions";
    if (actions.includes("REVISE")) {
      const continuation = document.createElement("p");
      continuation.textContent = tr("修订会沿用新值、来源和变更原因，并关联原提案。请按当前依据补齐资料；新提案需要重新预演与批准。", "Revision retains the proposed value, source, and reason, and links to this proposal. Update the evidence against the current base; the new proposal needs a fresh preview and approval.");
      body.append(continuation);
    }
    const actionLabels = { PREVIEW: ["查看变更影响", "Check change impact"], APPROVE: ["确认并批准", "Approve exact proposal"], APPLY: ["应用已批准提案", "Apply approved proposal"], REVISE: ["修订为新提案", "Revise as new proposal"] };
    for (const action of actions.filter((value) => actionLabels[value])) {
      const button = document.createElement("button"); button.type = "button"; button.className = "button button-secondary";
      button.textContent = action === "APPLY" && recovering ? tr("恢复结果记录", "Recover result record") : tr(...actionLabels[action]);
      const unavailable = action === "APPROVE" && (!certificate || acknowledgementDigest !== preview.preview_digest);
      button.dataset.unavailable = String(unavailable); button.disabled = busy || unavailable;
      if (action === "APPROVE") button.id = "change-approve-exact";
      button.addEventListener("click", () => action === "REVISE" ? revise(event) : command(action)); controls.append(button);
    }
    body.append(controls);
    if (actions.includes("REJECT")) {
      const form = document.createElement("form"); form.className = "change-decision-form";
      const label = document.createElement("label"); label.textContent = tr("拒绝原因", "Reason for rejection");
      const reason = document.createElement("textarea"); reason.id = "change-detail-reason"; reason.required = true; reason.maxLength = 1000; reason.rows = 3; label.append(reason);
      const button = document.createElement("button"); button.className = "button button-secondary"; button.type = "submit"; button.textContent = tr("记录拒绝", "Reject with reason"); button.disabled = busy;
      form.append(label, button); form.addEventListener("submit", (e) => { e.preventDefault(); if (reason.value.trim()) command("REJECT", { reason: reason.value.trim() }); }); body.append(form);
    }
    renderAuthority(body, actions);
    const audit = document.createElement("details"); audit.id = "change-audit";
    const summary = document.createElement("summary"); summary.id = "change-audit-label"; summary.textContent = tr("审计标识与依据版本", "Audit identity and base version");
    const pre = document.createElement("pre"); pre.textContent = JSON.stringify({ event_id: event.event_id, base_version: event.base_version, base_digest: event.base_digest, preview_digest: preview && preview.preview_digest, approval_digest: detail.approval && detail.approval.approval_digest }, null, 2); audit.append(summary, pre); body.append(audit);
    updateReviewButton();
    restoreDetail();
    resumeReview();
    window.dispatchEvent(new CustomEvent("orgrebase:changeselection"));
  }
  function renderAuthority(body, actions) {
    const authority = detail.authority;
    const available = actions.filter(action => ["DELEGATE", "REVOKE_DELEGATION", "ESCALATE"].includes(action));
    if (!authority || (!available.length && !authority.delegation && !authority.escalation)) return;
    const section = document.createElement("details"), summary = document.createElement("summary");
    section.id = "change-authority"; summary.id = "change-authority-label";
    summary.textContent = tr("责任委派与协调", "Delegation and coordination"); section.append(summary);
    const facts = document.createElement("dl");
    if (authority.delegation) {
      addFact(facts, tr("委派对象", "Delegate"), authority.delegation.delegate.actor_id);
      addFact(facts, tr("委派有效期", "Delegation expiry"), authority.delegation.expires_at);
      addFact(facts, tr("委派状态", "Delegation status"), authority.delegation_active === true ? tr("本提案有效", "Active for this proposal") : tr("已失效或撤销", "Expired or revoked"));
      if (authority.blocked_reason) addFact(facts, tr("限制原因", "Blocker"), authority.blocked_reason, true);
    }
    if (authority.escalation) addFact(facts, tr("协调请求", "Coordination request"), authority.escalation.reason, true);
    section.append(facts);
    const boundary = document.createElement("p"); boundary.textContent = tr("委派只适用于这一份提案且有有效期。升级仅请求组织协调，不授予审批权；原责任人与既有决定保留。", "Delegation is time limited and applies only to this proposal. Escalation requests coordination without granting approval authority. Original responsibility and prior decisions remain recorded."); section.append(boundary);
    for (const action of available) {
      const form = document.createElement("form"); form.className = "change-decision-form";
      const input = (id, title, type = "text") => {
        const label = document.createElement("label"), caption = document.createElement("span"), field = document.createElement(type === "textarea" ? "textarea" : "input");
        caption.textContent = title; field.id = id; if (type !== "textarea") field.type = type; field.required = true; field.maxLength = type === "textarea" ? 1000 : 256; label.append(caption, field); form.append(label); return field;
      };
      let member = null, duration = null;
      const eligible = Array.isArray(authority.eligible_delegates) ? authority.eligible_delegates : [];
      const memberKey = entry => JSON.stringify([entry.subject, entry.actor_id]);
      if (action === "DELEGATE") {
        if (!eligible.length) {
          const hint = document.createElement("p"); hint.textContent = tr("当前没有可委派的审批成员。请管理员先配置本工作区的审批成员。", "No eligible reviewer is available. Ask an administrator to configure approvers for this workspace."); section.append(hint); continue;
        }
        const label = document.createElement("label"), caption = document.createElement("span"); caption.textContent = tr("委派给", "Delegate to"); member = document.createElement("select"); member.id = "change-delegate-member"; member.required = true;
        const empty = document.createElement("option"); empty.value = ""; empty.textContent = tr("选择有审批资格的成员", "Choose an eligible reviewer"); member.append(empty);
        eligible.forEach(entry => { const option = document.createElement("option"); option.value = memberKey(entry); option.textContent = entry.label || entry.actor_id; member.append(option); }); label.append(caption, member); form.append(label);
        duration = input("change-delegate-duration", tr("有效时间（分钟）", "Validity (minutes)"), "number"); duration.min = "1"; duration.max = "60"; duration.value = "15";
      }
      const reason = input(`change-${action.toLowerCase()}-reason`, tr("原因", "Reason"), "textarea");
      const button = document.createElement("button"); button.type = "submit"; button.className = "button button-secondary";
      button.textContent = action === "DELEGATE" ? tr("确认本次委派", "Delegate this proposal") : action === "REVOKE_DELEGATION" ? tr("撤销本次委派", "Revoke delegation") : tr("请求协调升级", "Request escalation");
      button.disabled = busy; form.append(button);
      form.addEventListener("submit", e => {
        e.preventDefault(); if (!reason.value.trim()) return;
        const extra = {reason:reason.value.trim()};
        if (action === "DELEGATE") {
          const recipient = eligible.find(entry => memberKey(entry) === member.value);
          if (member.value === "" || !recipient?.subject || !recipient?.actor_id) return;
          const seconds = Number(duration.value) * 60; if (!Number.isSafeInteger(seconds) || seconds < 30 || seconds > 3600) return;
          Object.assign(extra, {subject:recipient.subject, actor_id:recipient.actor_id, valid_seconds:seconds});
        }
        coordinate(action, extra);
      }); section.append(form);
    }
    body.append(section);
  }
  async function coordinate(action, extra) {
    if (busy || !detail?.allowed_actions.includes(action) || !detail.event.digest) return;
    const paths = {DELEGATE:"delegate", REVOKE_DELEGATION:"revoke-delegation", ESCALATE:"escalate"};
    const current = detail, session = client.session(), workspaceId = client.workspace?.();
    const sameContext = () => client.session() === session && client.workspace?.() === workspaceId
      && selectedId === current.event.event_id && options?.execution_run_id === current.execution_run_id
      && (!state || state.execution?.run_id === current.execution_run_id);
    const id = current.event.event_id, body = {...extra, event_digest:current.event.digest};
    const commandToken = ++commandSequence;
    setBusy(true);
    try {
      await client.json(`/api/workspace/changes/${encodeURIComponent(id)}/${paths[action]}`, {method:"POST", body});
      if (!sameContext()) return;
      acknowledgementDigest = null;
      const refreshed = await refresh();
      if (!sameContext() || !refreshed) return;
      showNotice(tr("责任协调已记录。请刷新后的当前负责人继续审阅；本操作不产生批准或业务写入。", "Coordination recorded. The current responsible reviewer can continue review. This action does not approve or apply business changes."), "success");
      window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", { detail: { source: "change-workbench" } }));
    } catch (error) { if (sameContext()) fail(error); } finally { if (commandToken === commandSequence) setBusy(false); }
  }
  async function recoverEvidence(action, payload) {
    if (busy || !detail?.recovery?.allowed_actions?.includes(action)) return;
    const current = detail, session = client.session(), workspaceId = client.workspace();
    const id = current.event.event_id;
    const requestKey = JSON.stringify([session?.principal?.actor_id, workspaceId, current.execution_run_id, id, action, payload]);
    if (recoveryOperation?.key !== requestKey) recoveryOperation = {key: requestKey, id: `evidence:${crypto.randomUUID()}`};
    const operationId = recoveryOperation.id;
    const sameContext = () => client.session() === session && client.workspace() === workspaceId
      && options?.execution_run_id === current.execution_run_id && selectedId === id
      && (!state || state.execution?.run_id === current.execution_run_id);
    const path = action === "RETURN_FOR_EVIDENCE" ? "return-for-evidence" : "resume";
    const commandToken = ++commandSequence;
    setBusy(true);
    const stopProgress = action === "RESUME" ? watchPreview(current, session, workspaceId) : () => {};
    try {
      await client.json(`/api/workspace/changes/${encodeURIComponent(id)}/${path}`, {
        method: "POST", body: {operation_id: operationId, ...payload},
      });
      if (!sameContext()) return;
      acknowledgementDigest = null; activeReviewMs = 0;
      if (!await refresh() || !sameContext()) return;
      const next = detail.recovery?.state;
      const message = action === "RETURN_FOR_EVIDENCE"
        ? tr("已退回指定任务补证，原变更与上下文已保留。", "The selected task was returned for evidence; the change and context are preserved.")
        : next === "READY_FOR_REVIEW"
          ? tr("补证任务已完成。请负责人审阅新候选，再批准或拒绝。", "The evidence task completed. The reviewer can now assess the new candidate and approve or reject it.")
          : next === "FAILED"
            ? tr("本轮补证执行未完成。请负责人查看失败记录，核对后发起新一轮补证。", "This evidence attempt did not complete. The reviewer should inspect the failure and request a new round if appropriate.")
            : tr("继续请求已记录，请查看当前任务状态。", "The continuation request was recorded. Check the current task status.");
      showNotice(message, next === "FAILED" ? "error" : "success");
      window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", {detail: {source: "change-workbench"}}));
    } catch (error) {
      if (sameContext() && error.code !== "WORKSPACE_REQUEST_CONTEXT_CHANGED") {
        await refresh();
        if (sameContext()) fail(error);
      }
    } finally { stopProgress(); if (commandToken === commandSequence) setBusy(false); }
  }
  async function select(eventId, { focus = false } = {}) {
    pauseReview();
    const sequence = ++selectionSequence;
    selectionPending = true;
    try {
      const result = await client.json(`/api/workspace/changes/${encodeURIComponent(eventId)}`);
      if (sequence !== selectionSequence) return false;
      if (result?.event?.event_id !== eventId || result.execution_run_id !== options?.execution_run_id) throw new Error("CHANGE_DETAIL_RESPONSE_INVALID");
      if (selectedId !== eventId || result.preview?.preview_digest !== detail?.preview?.preview_digest) { acknowledgementDigest = null; activeReviewMs = 0; }
      selectedId = eventId; detail = result; renderList(); renderDetail();
      if (focus) { byId("change-detail-title").focus(); resumeReview(); }
      return true;
    } catch (error) {
      if (sequence === selectionSequence) { readFailure = true; invalidateReadModel(); fail(error); }
      return false;
    }
    finally { if (sequence === selectionSequence) selectionPending = false; }
  }
  function refreshSelectedChange() {
    if (busy || selectionPending || !detail || detail.event.event_id !== selectedId
      || detail.execution_run_id !== options?.execution_run_id || detail.execution_run_id !== state?.execution?.run_id) return;
    const event = Array.isArray(state.change_events) && state.change_events.find(item => item.event_id === selectedId);
    if (event?.event_digest && event.event_digest === detail.event.digest
      && (typeof event.status === "string" && event.status !== detail.status
        || typeof event.authority_revision === "string" && event.authority_revision !== detail.authority?.revision)) select(selectedId);
  }
  async function refresh({ append = false } = {}) {
    if (refreshPromise) return refreshPromise;
    const sequence = ++loadSequence;
    const pending = (async () => {
      try {
        const [configuration, page] = await Promise.all([client.json("/api/workspace/change-options"), client.json(`/api/workspace/changes?after=${append && nextCursor != null ? nextCursor : 0}&limit=20`)]);
        if (sequence !== loadSequence) return;
        if (!configuration.execution_run_id || !Array.isArray(configuration.fields) || !Array.isArray(page.items)) throw new Error("CHANGE_EDITOR_RESPONSE_INVALID");
        // A response for a superseded run cannot publish data or an error into
        // the replacement context. State rendering will refresh its read model.
        if (state?.execution?.run_id && state.execution.run_id !== configuration.execution_run_id) return;
        if (observedRunId && observedRunId !== configuration.execution_run_id) { draft = null; draftEdited = false; selectedId = null; detail = null; events = []; readFailure = false; }
        observedRunId = configuration.execution_run_id;
        options = configuration;
        events = append ? [...events, ...page.items.filter((item) => !events.some((old) => old.event.event_id === item.event.event_id))] : page.items;
        nextCursor = page.next_cursor; renderOptions(); renderList();
        const eventId = selectedId || events[0]?.event.event_id;
        if (eventId && !await select(eventId)) return false;
        setBusy(busy);
        if (readFailure) {
          readFailure = false;
          showNotice(tr("已重新核对当前状态，可以继续操作。未提交草稿已保留，系统未重发任何操作。", "Current state refreshed. You can continue; unsent drafts are retained and no commands were resent."), "info");
        }
        return true;
      } catch (error) {
        if (sequence === loadSequence) { readFailure = true; invalidateReadModel(); fail(error); }
        return false;
      }
      finally { if (refreshPromise === pending) refreshPromise = null; }
    })();
    refreshPromise = pending;
    return refreshPromise;
  }
  async function revise(event) {
    const current = detail;
    if (!await refresh()) return;
    if (detail?.event.event_id !== event.event_id || detail?.execution_run_id !== current?.execution_run_id) return;
    if (!detail.allowed_actions?.includes("REVISE")) {
      showNotice(tr("提案状态或权限已变化，暂不能创建修订草稿。请核对当前记录。", "The proposal state or your permissions changed. A revision draft cannot be created; review the current record."));
      return;
    }
    const selected = options?.fields.find((item) => item.slot_id === event.slot_id);
    if (!selected || !selected.allowed_operations.length) { showNotice(tr("该字段已不在当前可编辑范围。", "This field is no longer editable.")); return; }
    byId("change-field").value = event.slot_id; newDraft();
    draftEdited = true;
    draft.value = selected.value_kind === "pricing_policy"
      ? JSON.parse(JSON.stringify(event.proposal.payload.canonical_value)) : valueText(event.proposal.payload.canonical_value);
    draft.source_ref = event.proposal.source_refs[0] || "";
    draft.reason = current?.reason || null;
    draft.revises_event_id = event.event_id;
    renderEditor(); byId("change-editor").open = true; byId("change-value").focus();
    showNotice(tr("已建立独立草稿。原拒绝和审批记录保留；此提案需重新预演与批准。", "A separate draft is ready. Prior decisions remain unchanged; this proposal needs a new preview and approval."), "info");
  }
  async function submit(e) {
    e.preventDefault(); if (busy || !draft || byId("change-submit").dataset.unavailable === "true") return;
    const selected = field();
    if (!selected || selected.slot_id !== draft.slot_id || !selected.allowed_operations.includes(draft.operation)) { renderEditor(); return; }
    if (draft.base_digest !== selected.current.digest || draft.base_version !== selected.current.version) { fail(new Error("CHANGE_BASE_STALE")); return; }
    if (!draft.source_ref.trim()) return;
    let value = draft.value;
    try {
      if (selected.value_kind === "pricing_policy") {
        const entries = draft.pricing_input || {discount: basisPointsText(value.discount_bps), tax: basisPointsText(value.tax_bps), label: value.tax_label || ""};
        if (!entries.label.trim()) throw new Error("PRICING_TAX_LABEL_REQUIRED");
        value = {...value, discount_bps: percentBasisPoints(entries.discount), tax_bps: percentBasisPoints(entries.tax),
          tax_label: entries.label.trim(), source_ref: draft.source_ref};
      } else if (selected.value_kind === "json") {
        value = JSON.parse(value);
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("PRICING_OBJECT_REQUIRED");
      } else if (!value.trim()) return;
    } catch (_) {
      showNotice(selected.value_kind === "pricing_policy"
        ? tr("请填写 0–100 之间、最多两位小数的折扣率和税率，并说明税费口径。", "Enter discount and tax rates from 0 to 100 with at most two decimal places, and provide a tax basis label.")
        : tr("请输入有效的 JSON 对象；明细结构将在提交时核验。", "Enter a valid JSON object; the basket structure is validated on submission."));
      return;
    }
    draft.event_id ||= `ui:${crypto.randomUUID()}`;
    const submitted = { ...draft, value }; delete submitted.pricing_input;
    const session = client.session(), workspaceId = client.workspace?.(), runId = options?.execution_run_id;
    const sameContext = () => client.session() === session && client.workspace?.() === workspaceId
      && options?.execution_run_id === runId && (!state || state.execution?.run_id === runId);
    const commandToken = ++commandSequence;
    setBusy(true);
    try {
      const result = await client.json("/api/workspace/change-proposals", { method: "POST", body: submitted });
      if (!sameContext()) return;
      if (result.event_id !== submitted.event_id) throw new Error("CHANGE_PROPOSAL_RESPONSE_ID_MISMATCH");
      selectedId = submitted.event_id;
      if (draft?.event_id === submitted.event_id) {
        draft = null;
        draftEdited = false;
        byId("change-editor").open = false;
      }
      showNotice(tr("提案已登记，业务值尚未改变。请预演影响，再由负责人审阅。", "Proposal registered; business values are unchanged. Preview effects, then request owner review."), "success");
      await refresh(); window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", { detail: { source: "change-workbench" } }));
    } catch (error) { if (sameContext()) fail(error); }
    finally { if (commandToken === commandSequence) setBusy(false); }
  }
  function watchPreview(current, session, workspaceId) {
    if (typeof AbortController === "undefined" || typeof setTimeout === "undefined") return () => {};
    let stopped = false, timer = null, controller = null;
    const sameContext = () => !stopped && client.session() === session && client.workspace() === workspaceId
      && selectedId === current.event.event_id && options?.execution_run_id === current.execution_run_id
      && (!state || state.execution?.run_id === current.execution_run_id);
    const contextEvents = ["orgrebase:sessionended", "orgrebase:staterendered", "orgrebase:workspacechange"];
    const stop = () => {
      stopped = true; clearTimeout(timer); controller?.abort();
      for (const event of contextEvents) window.removeEventListener(event, contextChanged);
      window.removeEventListener("pagehide", stop);
    };
    const contextChanged = () => { if (!sameContext()) stop(); };
    const poll = async () => {
      if (!sameContext()) return;
      if (document.visibilityState !== "visible") { timer = setTimeout(poll, 2000); return; }
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 5000);
      try {
        const result = await client.json(`/api/workspace/changes/${encodeURIComponent(current.event.event_id)}`,
          { signal: controller.signal });
        if (sameContext() && result.execution_run_id === current.execution_run_id
          && result.event?.event_id === current.event.event_id && result.event.digest === current.event.digest) {
          detail = result;
          renderDetail();
        }
      } catch (_) { /* Progress reads never retry the preview command. */ }
      finally {
        clearTimeout(timeout);
        controller = null;
        if (sameContext()) timer = setTimeout(poll, 2000);
      }
    };
    for (const event of contextEvents) window.addEventListener(event, contextChanged);
    window.addEventListener("pagehide", stop);
    timer = setTimeout(poll, 1000);
    return stop;
  }
  async function command(action, extra = {}) {
    if (busy || !detail || !detail.allowed_actions.includes(action)) return;
    const current = detail;
    const id = current.event.event_id;
    const session = client.session();
    const principal = session?.principal;
    const actor = principal?.actor_id || current.event.owner_id;
    const headers = session?.mode === "local" ? { "X-OrgRebase-Actor": actor } : {};
    const routes = { PREVIEW: `preview/${encodeURIComponent(id)}`, APPROVE: `approve/${encodeURIComponent(id)}`, APPLY: `apply/${encodeURIComponent(id)}`, REJECT: `changes/${encodeURIComponent(id)}/reject` };
    if (action === "APPROVE" && (acknowledgementDigest !== current.preview?.preview_digest || byId("change-approve-exact")?.disabled)) return;
    const body = action === "APPROVE" ? { actor_id: actor, preview_digest: current.preview.preview_digest,
      ...(current.recovery?.recovery_digest ? { recovery_digest: current.recovery.recovery_digest } : {}) }
      : action === "APPLY" ? { approval_digest: current.approval.approval_digest }
        : action === "REJECT" ? { actor_id: actor, ...extra } : undefined;
    const commandToken = ++commandSequence;
    setBusy(true);
    let reviewedMs = activeReviewMs;
    activeReviewMs = 0;
    const workspaceId = client.workspace();
    const requireCommandContext = () => {
      if (client.session() !== session || client.workspace() !== workspaceId
        || options?.execution_run_id !== current.execution_run_id
        || state && state.execution?.run_id !== current.execution_run_id) {
        throw Object.assign(new Error("WORKSPACE_REQUEST_CONTEXT_CHANGED"), { code: "WORKSPACE_REQUEST_CONTEXT_CHANGED" });
      }
    };
    const stopProgress = action === "PREVIEW" ? watchPreview(current, session, workspaceId) : () => {};
    let approvalRecorded = false;
    let operation = action;
    try {
      const result = await client.json(`/api/workspace/${routes[action]}`, { method: "POST", headers, body });
      if (action === "APPROVE") approvalRecorded = true;
      requireCommandContext();
      await observation(action, "SUCCESS", null, current, reviewedMs); acknowledgementDigest = null;
      reviewedMs = 0;
      requireCommandContext();
      let message = action === "APPLY" && current.status === "RECOVERY_REQUIRED"
        ? tr("结果记录已恢复，未重复执行业务变更。", "Result record recovered; the business change was not repeated.")
        : tr("操作已记录。", "Action recorded.");
      if (action === "APPROVE") {
        requireCommandContext();
        const approved = await client.json(`/api/workspace/changes/${encodeURIComponent(id)}`);
        requireCommandContext();
        if (!result?.approval_digest || approved.execution_run_id !== current.execution_run_id
          || approved.event?.event_id !== id || approved.event.digest !== current.event.digest
          || approved.preview?.preview_digest !== current.preview.preview_digest
          || approved.approval?.approval_digest !== result.approval_digest || approved.status !== "APPROVED") {
          throw new Error("CHANGE_APPROVAL_CONTINUATION_MISMATCH");
        }
        message = tr("提案已批准，等待有执行权限的账号应用。", "Proposal approved; awaiting an account with execution permission.");
        if (approved.allowed_actions?.includes("APPLY")) {
          operation = "APPLY";
          showNotice(tr("批准已记录，正在应用这份提案…", "Approval recorded; applying this proposal…"), "info");
          await client.json(`/api/workspace/${routes.APPLY}`, { method: "POST", body: { approval_digest: result.approval_digest } });
          requireCommandContext();
          await observation("APPLY", "SUCCESS", null, approved, reviewedMs);
          requireCommandContext();
          message = tr("精确提案已批准，选择性重构已完成。", "Exact proposal approved; selective Rebase completed.");
        }
      }
      showNotice(message, "success");
      await refresh();
      byId("change-detail-title").focus();
      window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", { detail: { source: "change-workbench" } }));
    } catch (error) {
      if (error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
      try { requireCommandContext(); } catch (_) { return; }
      if (approvalRecorded || action === "APPLY") {
        try { requireCommandContext(); } catch (_) { return; }
        if (operation === "APPLY") await observation("APPLY", "ERROR", /^[A-Z0-9_:-]{1,128}$/.test(error.code || "") ? error.code : "REQUEST_FAILED", current, reviewedMs);
        try { requireCommandContext(); } catch (_) { return; }
        await refresh();
        try { requireCommandContext(); } catch (_) { return; }
        const sameChange = detail?.execution_run_id === current.execution_run_id
          && detail.event?.event_id === id && detail.event.digest === current.event.digest;
        if (sameChange && detail.status === "RECOVERY_REQUIRED") {
          showNotice(tr("变更已提交，需恢复结果记录。请使用下方恢复操作，不要再次提案或批准。", "The change is committed and its result record needs recovery. Use the recovery action below; do not propose or approve again."));
        } else if (sameChange && detail.status === "APPLIED") {
          showNotice(tr("已核对当前记录：变更已应用。", "Current records confirm the change was applied."), "success");
        } else {
          showNotice(tr("批准已记录，应用结果尚未确认。请核对下方状态；仍待应用时可重试应用，无需再次批准。", "Approval is recorded; application is not confirmed. Check the current status below and retry Apply if still pending; do not approve again."));
        }
        window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", { detail: { source: "change-workbench" } }));
      } else {
        await observation(action, "ERROR", /^[A-Z0-9_:-]{1,128}$/.test(error.code || "") ? error.code : "REQUEST_FAILED", current, reviewedMs);
        try { requireCommandContext(); } catch (_) { return; }
        fail(error);
      }
    }
    finally { stopProgress(); if (commandToken === commandSequence) setBusy(false); }
  }
  function translate() {
    const copy = {
      "change-workbench-title": ["变化工作台", "Change workbench"], "change-workbench-intro": ["提出变化、核对影响，再由对应负责人批准。所有决定绑定具体提案。", "Propose a change, review its effects, and obtain the matching owner's approval. Every decision binds an exact proposal."],
      "change-refresh": ["刷新状态", "Refresh"], "change-editor-summary": ["提出变化", "Propose a change"], "change-field-label": ["上游来源字段", "Upstream source field"], "change-operation-label": ["处理方式", "Operation"],
      "change-current-label": ["当前值", "Current value"], "change-owner-label": ["对应负责人", "Responsible owner"], "change-base-label": ["依据版本", "Base version"], "change-value-label": ["建议的新值", "Proposed value"], "change-source-label": ["新事实依据（来源引用）", "Evidence for the new fact (source reference)"],
      "change-reason-label": ["变更原因（可选）", "Reason for change (optional)"],
      "change-editor-boundary": ["按领域查看上游来源；标为“当前不可提案”的字段仍可查看当前值和限制原因。提交只登记提案，不修改现行事实；来源失效时必须重新准入。", "Inspect upstream sources by domain. Fields marked “proposal unavailable” still show their current values and restrictions. Submission records a proposal without changing current facts. Invalidated sources require readmission."], "change-submit": ["登记提案", "Register proposal"], "change-cancel": ["清空草稿", "Clear draft"], "change-list-title": ["变化记录", "Changes"], "change-list-empty": ["尚无变化提案。", "No change proposals yet."], "change-load-more": ["加载更多", "Load more"],
    };
    for (const [id, value] of Object.entries(copy)) byId(id).textContent = tr(...value);
    if (options) renderOptions(); renderList(); renderDetail();
  }
  byId("change-field").addEventListener("change", newDraft);
  byId("change-source").addEventListener("input", editDraft);
  byId("change-proposal-reason").addEventListener("input", editDraft);
  byId("change-operation").addEventListener("change", () => { editDraft(); renderEditor(); });
  byId("change-proposal-form").addEventListener("submit", submit);
  byId("change-cancel").addEventListener("click", () => {
    const selected = field();
    if (draft && selected && draft.base_digest !== selected.current.digest) { draft.base_digest = selected.current.digest; draft.base_version = selected.current.version; draft.event_id = null; renderEditor(); showNotice(tr("已使用当前依据，请重新核对输入。", "Current base selected. Review your input again."), "info"); }
    else { newDraft(); showNotice(""); }
    setBusy(false);
  });
  byId("change-refresh").addEventListener("click", () => window.dispatchEvent(new CustomEvent("orgrebase:workspacechange")));
  byId("change-load-more").addEventListener("click", () => refresh({ append: true }));
  window.addEventListener("orgrebase:staterendered", (event) => {
    if (observedRunId && event.detail?.execution?.run_id && observedRunId !== event.detail.execution.run_id) invalidateReadModel({ clearDraft: true });
    state = event.detail; renderChangeResult(byId("change-result")); renderPricingPreview(byId("change-preview-pricing"));
    if (!options && state) refresh(); else refreshSelectedChange();
  });
  window.addEventListener("orgrebase:stateunavailable", () => {
    readFailure = true;
    invalidateReadModel();
    showNotice(tr("当前工作区状态不可用，已暂停变更操作。未提交的草稿保留；刷新确认状态后继续。", "Workspace state is unavailable; change actions are paused. Your unsent draft is retained. Refresh to confirm the state before continuing."));
  });
  window.addEventListener("orgrebase:workspacechange", (event) => {
    if (event.detail?.source !== "change-workbench") refresh();
  });
  window.addEventListener("orgrebase:languagechange", translate);
  document.addEventListener("visibilitychange", resumeReview); document.addEventListener("focusin", resumeReview); window.addEventListener("focus", resumeReview); window.addEventListener("blur", pauseReview);
  window.addEventListener("orgrebase:sessionchange", event => {
    const session = event.detail;
    if (!session || session.authentication_required && !session.authenticated) return;
    const next = sessionIdentity(session);
    if (draftSessionIdentity !== null && next !== draftSessionIdentity) invalidateReadModel({ clearDraft: true });
    // Keep the last usable identity across expiry, so reauthentication can
    // retain its own draft without exposing it to a different account.
    draftSessionIdentity = next;
  });
  window.addEventListener("orgrebase:sessionended", (event) => {
    const roleSwitch = event.detail?.reason === "role-switch";
    const selectedChange = roleSwitch ? selectedId : null;
    const clearDraft = event.detail?.reason === "logout" || roleSwitch;
    invalidateReadModel({ clearDraft, retireCommands: true });
    if (roleSwitch) draftSessionIdentity = sessionIdentity(client.session());
    if (clearDraft) readFailure = false;
    selectedId = selectedChange;
  });
  window.OrgRebaseChangeWorkbench = Object.freeze({ refresh, renderAdvisoryAttempt, authorityProgress,
    selectedDetail: () => detail?.event.event_id === selectedId && detail?.execution_run_id === options?.execution_run_id ? detail : null,
    select: async (id) => { window.OrgRebaseWorkspaceShell?.navigate("data"); await select(id, { focus: true }); } });
  translate();
  refresh();
})();
