(() => {
  "use strict";
  const client = window.OrgRebaseClient;
  const mount = document.getElementById("task-changes-flow");
  if (!client || !mount) return;
  const root = document.createElement("section");
  root.id = "effect-workbench"; root.className = "change-workbench";
  root.setAttribute("aria-labelledby", "effect-title");
  root.innerHTML = `<header class="change-workbench-header"><div><h2 id="effect-title"></h2><p id="effect-intro"></p></div><button class="button button-secondary" id="effect-refresh" type="button"></button></header>
    <p id="effect-notice" class="change-workbench-notice" role="status" aria-live="polite" hidden></p>
    <details id="effect-editor" class="change-editor"><summary id="effect-editor-label"></summary><p id="effect-editor-unavailable"></p><form id="effect-form" hidden>
      <label><span id="effect-observation-label"></span><select id="effect-observation" required></select></label><p id="effect-observation-context"></p><div id="effect-fields" class="change-form-grid"></div>
      <label><span id="effect-reason-label"></span><textarea id="effect-reason" required maxlength="1000" rows="3"></textarea></label>
      <div class="change-workbench-actions"><button class="button button-primary" id="effect-submit" type="submit"></button><button class="button button-secondary" id="effect-clear" type="button"></button></div>
    </form></details>
    <div class="change-workbench-layout"><section aria-labelledby="effect-list-title"><h3 id="effect-list-title"></h3><ul id="effect-list" class="change-event-list"></ul><p id="effect-empty"></p><button id="effect-more" class="button button-secondary" type="button" hidden></button></section><section id="effect-detail" class="change-detail" aria-labelledby="effect-detail-title"><h3 id="effect-detail-title" tabindex="-1"></h3><p id="effect-detail-unavailable" role="status" hidden></p><div id="effect-detail-body"></div></section></div>`;
  mount.append(root);
  const byId = id => document.getElementById(id);
  const tr = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const stringify = value => value == null ? "—" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const names = {name:["草稿名称", "Draft name"], description:["草稿说明", "Draft description"]};
  const statuses = {PENDING_APPROVAL:["等待负责人批准", "Awaiting owner approval"], READY:["已批准，尚未发送", "Approved, not sent"], IN_FLIGHT:["正在执行，结果待确认", "Executing; confirmation pending"], COMMIT_UNKNOWN:["结果未知，须核查或安全取消", "Unknown; reconcile or safely cancel"], CONFIRMED:["目标已确认提交", "Target confirmed the effect"], REJECTED:["未执行或已被安全取消", "Rejected or safely cancelled"]};
  let configuration = null, items = [], selected = null, nextCursor = null, busy = false;
  let generation = 0, selectionSequence = 0, draft = null, acknowledged = null;
  let configurationCurrent = false, detailCurrent = false;
  const sessionIdentity = session => session ? JSON.stringify([session.mode, session.authenticated,
    session.principal?.tenant_id, session.principal?.actor_id,
    session.principal?.issuer, session.principal?.subject]) : null;
  let sessionKey = sessionIdentity(client.session?.()), workspaceId = client.workspace?.() || null, sessionRevision = 0;
  function clearSessionView(preserveDraft = false) {
    ++generation; ++selectionSequence; ++sessionRevision;
    configurationCurrent = false; configuration = null; selected = null; items = []; nextCursor = null; acknowledged = null; busy = false;
    if (!preserveDraft) {
      draft = null;
      byId("effect-fields").replaceChildren(); byId("effect-reason").value = "";
      byId("effect-observation").replaceChildren(); byId("effect-observation-context").textContent = "";
    }
    notice(""); markDetailCurrent(false); renderEditor(); renderList(); renderDetail();
  }
  function syncWorkspace() {
    const next = client.workspace?.() || null;
    const changed = next !== null && workspaceId !== null && next !== workspaceId;
    if (changed) clearSessionView();
    if (next !== null) workspaceId = next;
    return changed;
  }
  function notice(value, tone = "info") { byId("effect-notice").textContent = value; byId("effect-notice").hidden = !value; byId("effect-notice").dataset.tone = tone; }
  function error(failure) {
    if (failure.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
    const code = failure.code || failure.message || "UNAVAILABLE";
    notice(tr("请求未确认。请刷新核对；不会自动重发。", "Request not confirmed. Refresh to reconcile; no automatic replay occurs.") + ` (${code})`, "error");
  }
  function setBusy(value) {
    busy = value; root.setAttribute("aria-busy", String(value));
    root.querySelectorAll("button").forEach(button => { button.disabled = value || button.dataset.unavailable === "true" || button.dataset.requiresCurrent === "true" && !detailCurrent; });
    byId("effect-submit").disabled ||= !configurationCurrent;
    byId("effect-form").querySelectorAll("input, select, textarea").forEach(input => { input.disabled = value; });
  }
  function rememberFocus(container) {
    const active = document.activeElement;
    if (!active?.id || !container.contains(active)) return () => {};
    const {id, selectionStart, selectionEnd} = active;
    return () => {
      const replacement = byId(id);
      if (!replacement || !container.contains(replacement)) { byId("effect-detail-title").focus(); return; }
      if (replacement.disabled) { byId("effect-refresh").focus(); return; }
      replacement.focus();
      if (typeof selectionStart === "number") replacement.setSelectionRange?.(selectionStart, selectionEnd);
    };
  }
  function markDetailCurrent(value) {
    detailCurrent = value;
    byId("effect-detail-unavailable").hidden = !selected || value;
    byId("effect-detail-unavailable").textContent = tr("当前操作状态尚未重新确认。请刷新操作记录后再审批、发送、查询或取消；下方仅保留上次读取的详情。", "The current operation state has not been reconfirmed. Refresh operation records before approving, sending, querying, or cancelling. The details below are the last successfully read version.");
    setBusy(busy);
  }
  function observation() { return configuration?.observations.find(item => item.observation_id === draft?.observation_id); }
  function fieldName(key) { return names[key] ? tr(...names[key]) : key; }
  function resetDraft() {
    const current = configuration?.observations.find(item => item.current === true);
    draft = current ? {proposal_id:null, observation_id:current.observation_id, changes:{}, reason:""} : null;
    renderEditor();
  }
  function renderEditor() {
    const restoreFocus = rememberFocus(byId("effect-form"));
    const current = configuration?.observations.filter(item => item.current === true) || [];
    const available = configuration?.can_propose === true && current.length > 0;
    byId("effect-form").hidden = !available;
    byId("effect-editor-unavailable").hidden = available;
    byId("effect-editor-unavailable").textContent = configuration?.can_propose === false
      ? tr("当前账号不能提出外部操作。", "This account cannot propose external operations.")
      : tr("后台须先读取并记录目标的当前草稿；没有有效观察时不能提出写入。", "A worker must first observe the current target draft. A valid observation is required before proposing a write.");
    if (!available || !draft) return;
    const chooser = byId("effect-observation"); chooser.replaceChildren();
    for (const item of current) { const option = document.createElement("option"); option.value = item.observation_id; option.textContent = `${item.observed_at} · ${item.version}`; chooser.append(option); }
    if (!current.some(item => item.observation_id === draft.observation_id)) {
      const stale = document.createElement("option"); stale.value = draft.observation_id; stale.textContent = tr("原观察已失效，请重新选择", "Original observation expired; select a current observation"); chooser.append(stale);
    }
    chooser.value = draft.observation_id;
    const observed = observation();
    byId("effect-observation-context").textContent = `${configuration.target_key}\n${observed?.version || "—"}`;
    byId("effect-fields").replaceChildren();
    for (const [key, contract] of Object.entries(configuration.fields)) {
      const label = document.createElement("label"); label.className = "wide";
      const caption = document.createElement("span"); caption.textContent = fieldName(key);
      const prior = document.createElement("small"); prior.textContent = tr("当前：", "Current: ") + stringify(observed?.fields[key]);
      const input = document.createElement(key === "description" ? "textarea" : "input"); input.id = `effect-field-${key}`; input.maxLength = contract.max_length; input.value = draft.changes[key] || "";
      input.setAttribute("aria-label", fieldName(key)); input.addEventListener("input", () => { draft.changes[key] = input.value; draft.proposal_id = null; });
      label.append(caption, prior, input); byId("effect-fields").append(label);
    }
    byId("effect-reason").value = draft.reason;
    byId("effect-submit").dataset.unavailable = String(observed?.current !== true);
    restoreFocus();
  }
  function fact(list, label, value) {
    const group = document.createElement("div"), term = document.createElement("dt"), detail = document.createElement("dd");
    term.textContent = label; detail.textContent = stringify(value); group.append(term, detail); list.append(group);
  }
  function renderDetail() {
    const body = byId("effect-detail-body"), restoreFocus = rememberFocus(body);
    const auditOpen = byId("effect-audit")?.open === true;
    body.replaceChildren();
    byId("effect-detail-title").textContent = selected ? (statuses[selected.state] ? tr(...statuses[selected.state]) : tr("状态待核查", "Status unverified")) : tr("选择外部操作", "Select an external operation");
    if (!selected) return;
    const proposal = selected.proposal;
    const facts = document.createElement("dl");
    fact(facts, tr("目标", "Target"), proposal.target_key);
    fact(facts, tr("操作", "Action"), tr("更新草稿名称或说明", "Update draft name or description"));
    fact(facts, tr("预期目标版本", "Expected target version"), proposal.expected_version);
    fact(facts, tr("负责人", "Owner"), proposal.owner_id);
    fact(facts, tr("操作原因", "Reason"), proposal.reason);
    for (const [key, value] of Object.entries(proposal.changes)) fact(facts, fieldName(key), `${stringify(proposal.previous_values[key])} → ${value}`);
    if (selected.blocked_reason || selected.effect?.last_error) fact(facts, tr("当前阻断原因", "Current blocker"), selected.blocked_reason || selected.effect.last_error);
    if (selected.effect?.requested_action) fact(facts, tr("等待后台处理", "Queued for the worker"), selected.effect.requested_action);
    body.append(facts);
    if (selected.state === "CONFIRMED") {
      const hint = document.createElement("p"); hint.textContent = tr("目标回执证明这次提交已完成。目标之后仍可能被其他操作修改；核对现状需要后台重新读取目标。", "The target receipt confirms this submission completed. Later operations may change the target; checking its current values requires a new worker observation."); body.append(hint);
    }
    if (selected.state === "COMMIT_UNKNOWN") {
      const hint = document.createElement("p"); hint.textContent = tr("目标可能已执行。查询只核对回执；安全取消会与原提交争用同一目标回执。取消被确认前，仍按结果未知处理。", "The target may already have executed. Query only reconciles receipts. Safe cancellation competes for the same target receipt; the result remains unknown until confirmed."); body.append(hint);
    }
    const actions = Array.isArray(selected.allowed_actions) ? selected.allowed_actions : [];
    if (actions.includes("APPROVE")) {
      const label = document.createElement("label"), check = document.createElement("input"); check.type = "checkbox"; check.id = "effect-approval-ack"; check.checked = acknowledged === selected.proposal_digest; check.style.width = "auto";
      check.addEventListener("change", () => { acknowledged = check.checked ? selected.proposal_digest : null; renderDetail(); byId("effect-approval-ack").focus(); });
      label.append(check, document.createTextNode(tr("已核对目标、版本与全部变更字段", "I reviewed the target, version, and every changed field"))); body.append(label);
    }
    const controls = document.createElement("div"); controls.className = "change-workbench-actions";
    const actionNames = {APPROVE:["批准外部操作", "Approve external operation"], REJECT:["拒绝此操作", "Reject operation"], EXECUTE:["发送已批准操作", "Send approved operation"], QUERY:["查询目标回执", "Query target receipt"], CANCEL:["请求安全取消", "Request safe cancellation"]};
    for (const action of actions.filter(item => actionNames[item])) {
      if (selected.state === "COMMIT_UNKNOWN" && action === "EXECUTE") continue;
      const button = document.createElement("button"); button.type = "button"; button.className = "button button-secondary"; button.textContent = tr(...actionNames[action]);
      button.id = `effect-action-${action.toLowerCase()}`; button.dataset.requiresCurrent = "true";
      button.dataset.unavailable = String(action === "APPROVE" && acknowledged !== selected.proposal_digest); button.disabled = busy || !detailCurrent || button.dataset.unavailable === "true";
      button.addEventListener("click", () => command(action)); controls.append(button);
    }
    if (canRevise()) {
      const button = document.createElement("button"); button.id = "effect-revise"; button.type = "button"; button.className = "button button-secondary";
      button.textContent = tr("修订后重新提案", "Revise as a new proposal"); button.dataset.requiresCurrent = "true"; button.disabled = busy || !detailCurrent;
      button.addEventListener("click", revise); controls.append(button);
    }
    body.append(controls);
    const audit = document.createElement("details"), summary = document.createElement("summary"), pre = document.createElement("pre"); audit.id = "effect-audit"; audit.open = auditOpen; summary.id = "effect-audit-label"; summary.textContent = tr("审批与目标回执", "Approval and target receipt"); pre.textContent = JSON.stringify({proposal_id:proposal.proposal_id, proposal_digest:selected.proposal_digest, approval:selected.approval, rejection:selected.rejection, effect:selected.effect}, null, 2); audit.append(summary, pre); body.append(audit);
    restoreFocus();
  }
  function canRevise() {
    return configurationCurrent && configuration?.can_propose === true && configuration.observations.some(item => item.current === true)
      && (selected?.state === "REJECTED" || selected?.state === "PENDING_APPROVAL" && selected.blocked_reason);
  }
  function revise() {
    if (busy || !detailCurrent || !canRevise()) return;
    const proposal = selected.proposal;
    resetDraft();
    draft.changes = Object.fromEntries(Object.entries(proposal.changes).filter(([key]) => Object.hasOwn(configuration.fields, key)));
    draft.reason = proposal.reason;
    acknowledged = null;
    renderEditor(); setBusy(false); byId("effect-editor").open = true;
    const firstField = Object.keys(configuration.fields)[0];
    (byId(`effect-field-${firstField}`) || byId("effect-reason")).focus();
    notice(tr("已按当前目标观察建立修订草稿。请重新核对全部字段；旧决定不变，新提案须重新批准和发送。", "A revision draft uses the current target observation. Review every field again; prior decisions remain unchanged, and the new proposal requires approval and dispatch."));
  }
  function renderList() {
    const restoreFocus = rememberFocus(byId("effect-list"));
    byId("effect-list").replaceChildren();
    for (const item of items) {
      const li = document.createElement("li"), button = document.createElement("button"); button.type = "button";
      button.id = `effect-list-${item.proposal.proposal_id}`;
      button.setAttribute("aria-current", String(item.proposal.proposal_id === selected?.proposal.proposal_id));
      button.textContent = `${Object.keys(item.proposal.changes).map(fieldName).join(" · ")} · ${statuses[item.state] ? tr(...statuses[item.state]) : tr("状态待核查", "Status unverified")}`;
      button.addEventListener("click", () => select(item.proposal.proposal_id, {focus:true})); li.append(button); byId("effect-list").append(li);
    }
    byId("effect-empty").hidden = items.length > 0; byId("effect-more").hidden = nextCursor == null;
    restoreFocus();
  }
  async function select(id, {focus = false} = {}) {
    const sequence = ++selectionSequence, context = generation;
    markDetailCurrent(false);
    try {
      const value = await client.json(`/api/workspace/effect-proposals/${encodeURIComponent(id)}`);
      if (sequence !== selectionSequence || context !== generation) return false;
      if (value.proposal.proposal_id !== id || value.proposal.target_key !== configuration.target_key) throw new Error("EFFECT_DETAIL_BINDING_MISMATCH");
      if (value.proposal_digest !== selected?.proposal_digest) acknowledged = null;
      selected = value; markDetailCurrent(true); renderList(); renderDetail();
      if (byId("effect-notice").dataset.tone === "error") notice("");
      if (focus) byId("effect-detail-title").focus();
      return true;
    } catch (failure) { if (sequence === selectionSequence && context === generation) { acknowledged = null; markDetailCurrent(false); error(failure); } return false; }
  }
  async function refresh(append = false) {
    syncWorkspace();
    const sequence = ++generation;
    const active = document.activeElement, restoreFocus = rememberFocus(root);
    configurationCurrent = false; markDetailCurrent(false);
    try {
      const [options, page] = await Promise.all([client.json("/api/workspace/effect-options"), client.json(`/api/workspace/effect-proposals?limit=20${append && nextCursor ? `&after=${encodeURIComponent(nextCursor)}` : ""}`)]);
      if (sequence !== generation) return;
      if (syncWorkspace()) return refresh();
      if (!Array.isArray(options.observations) || !Array.isArray(page.items)) throw new Error("EFFECT_RESPONSE_INVALID");
      if (configuration && configuration.target_key !== options.target_key) { draft = null; selected = null; acknowledged = null; }
      configuration = options; configurationCurrent = true; items = append ? [...items, ...page.items.filter(item => !items.some(old => old.proposal.proposal_id === item.proposal.proposal_id))] : page.items; nextCursor = page.next_cursor;
      if (!draft) resetDraft(); else renderEditor(); renderList();
      if (selected) await select(selected.proposal.proposal_id); else if (items.length) await select(items[0].proposal.proposal_id); else renderDetail();
      setBusy(busy);
    } catch (failure) {
      if (sequence !== generation) return;
      acknowledged = null; configurationCurrent = false; markDetailCurrent(false);
      if (failure.code === "EFFECT_CONFIGURATION_UNAVAILABLE" || failure.status === 404) notice(tr("本部署尚未配置外部目标；内部变化提案可继续使用。", "No external target is configured for this deployment. Internal change proposals remain available.")); else error(failure);
    } finally {
      if (sequence === generation && active && (document.activeElement === active || document.activeElement === document.body)) restoreFocus();
    }
  }
  async function submit(event) {
    event.preventDefault(); if (busy || !configurationCurrent || !draft || observation()?.current !== true) return;
    const changes = Object.fromEntries(Object.entries(draft.changes).filter(([,value]) => value.trim()));
    if (!Object.keys(changes).length || !draft.reason.trim()) return;
    draft.proposal_id ||= `external:${crypto.randomUUID()}`;
    const body = {...draft, changes}, revision = sessionRevision; setBusy(true);
    try {
      const response = await client.json("/api/workspace/effect-proposals", {method:"POST", body});
      if (revision !== sessionRevision) return;
      if (response.proposal.proposal_id !== body.proposal_id) throw new Error("EFFECT_PROPOSAL_RESPONSE_ID_MISMATCH");
      selected = response; draft = null; byId("effect-editor").open = false; acknowledged = null;
      notice(tr("外部操作提案已登记，尚未批准或发送。", "External operation proposed; it has not been approved or sent.")); await refresh();
    } catch (failure) { if (revision === sessionRevision) error(failure); } finally { if (revision === sessionRevision) setBusy(false); }
  }
  async function command(action) {
    if (busy || !detailCurrent || !selected?.allowed_actions.includes(action)) return;
    if (action === "APPROVE" && acknowledged !== selected.proposal_digest) return;
    if (selected.state === "COMMIT_UNKNOWN" && action === "EXECUTE") return;
    const id = selected.proposal.proposal_id, revision = sessionRevision; setBusy(true);
    try {
      const decision = ["APPROVE", "REJECT"].includes(action);
      await client.json(`/api/workspace/effect-proposals/${encodeURIComponent(id)}/${decision ? action.toLowerCase() : "actions"}`, {method:"POST", body:decision ? {proposal_digest:selected.proposal_digest} : {action, request_digest:selected.effect.request_digest}});
      if (revision !== sessionRevision) return;
      acknowledged = null;
      notice(decision ? tr("决定已记录，未自动发送外部操作。", "Decision recorded; no external operation was sent automatically.") : tr("请求已交给后台处理；请刷新核对目标回执，排队不代表执行成功。", "Request queued for the worker. Refresh to inspect the target receipt; queued does not mean successful."));
      await refresh();
    } catch (failure) { if (revision === sessionRevision) { acknowledged = null; markDetailCurrent(false); error(failure); } } finally { if (revision === sessionRevision) setBusy(false); }
  }
  function translate() {
    const copy = {"effect-title":["外部草稿操作", "External draft operations"], "effect-intro":["根据后台读取的目标版本提出操作，批准与发送分开。刷新仅更新本系统已保存的记录。", "Propose against the target version observed by the worker. Approval and dispatch are separate. Refresh loads records already saved in this system."], "effect-refresh":["刷新操作记录", "Refresh operation records"], "effect-editor-label":["提出外部草稿更新", "Propose an external draft update"], "effect-observation-label":["目标观察依据", "Target observation"], "effect-reason-label":["操作原因", "Reason"], "effect-submit":["登记外部提案", "Register external proposal"], "effect-clear":["清空并使用当前观察", "Clear and use current observation"], "effect-list-title":["外部操作记录", "External operations"], "effect-empty":["尚无外部操作。", "No external operations yet."], "effect-more":["加载更多", "Load more"]};
    for (const [id,value] of Object.entries(copy)) byId(id).textContent = tr(...value);
    renderEditor(); renderList(); renderDetail();
  }
  byId("effect-form").addEventListener("submit", submit);
  byId("effect-refresh").addEventListener("click", () => refresh()); byId("effect-more").addEventListener("click", () => refresh(true));
  byId("effect-clear").addEventListener("click", () => { resetDraft(); setBusy(false); });
  byId("effect-reason").addEventListener("input", () => { if (draft) { draft.reason = byId("effect-reason").value; draft.proposal_id = null; } });
  byId("effect-observation").addEventListener("change", () => { if (draft) { draft.observation_id = byId("effect-observation").value; draft.proposal_id = null; renderEditor(); setBusy(false); } });
  window.addEventListener("orgrebase:workspacechange", () => refresh()); window.addEventListener("orgrebase:languagechange", translate);
  window.addEventListener("orgrebase:sessionended", event => clearSessionView(event.detail?.reason === "expired"));
  window.addEventListener("orgrebase:sessionchange", event => {
    const available = event.detail && (!event.detail.authentication_required || event.detail.authenticated === true);
    const next = sessionIdentity(event.detail);
    if (!available || next !== sessionKey) clearSessionView(!available);
    // Retain the last usable identity across expiry, not across a different login.
    if (available) sessionKey = next;
  });
  window.OrgRebaseEffectWorkbench = Object.freeze({refresh});
  translate(); refresh();
})();
