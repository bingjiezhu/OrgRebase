(() => {
  "use strict";
  const client = window.OrgRebaseClient, mount = document.getElementById("task-changes-flow");
  if (!client || !mount) return;
  const base = "/api/workspace/organization/owner-changes";
  const root = document.createElement("section"); root.id = "owner-change-workbench"; root.className = "change-workbench";
  root.setAttribute("aria-labelledby", "owner-change-title");
  root.innerHTML = `<details id="owner-change-panel" class="change-editor"><summary id="owner-change-title"></summary>
    <p id="owner-change-intro"></p><button id="owner-change-refresh" class="button button-secondary" type="button"></button>
    <p id="owner-change-notice" class="change-workbench-notice" role="status" aria-live="polite" hidden></p>
    <p id="owner-change-policy"></p><p id="owner-change-account"></p>
    <details id="owner-change-editor"><summary id="owner-change-editor-title"></summary>
      <form id="owner-change-form"><div class="change-form-grid">
        <label><span id="owner-change-resource-label"></span><select id="owner-change-resource" required></select></label>
        <label><span id="owner-change-successor-label"></span><select id="owner-change-successor" required></select></label>
        <label class="wide"><span id="owner-change-reason-label"></span><textarea id="owner-change-reason" required maxlength="1000" rows="3"></textarea></label>
      </div><p id="owner-change-draft-context"></p><button id="owner-change-preview" class="button button-secondary" type="submit"></button></form>
      <section id="owner-change-preview-body" class="change-detail" aria-live="polite"></section>
      <div class="change-workbench-actions"><button id="owner-change-create" class="button button-primary" type="button" hidden></button><button id="owner-change-retry" class="button button-secondary" type="button" hidden></button><button id="owner-change-restart" class="button button-secondary" type="button" hidden></button></div>
    </details>
    <div class="change-workbench-layout"><section aria-labelledby="owner-change-list-title"><h3 id="owner-change-list-title"></h3>
      <ul id="owner-change-list" class="change-event-list"></ul><p id="owner-change-empty"></p><button id="owner-change-more" class="button button-secondary" type="button" hidden></button>
      <form id="owner-change-open-form" class="change-decision-form"><label for="owner-change-open-id" id="owner-change-open-label"></label><input id="owner-change-open-id" required maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9_.:-]*" autocomplete="off" /><button id="owner-change-open" class="button button-secondary" type="submit"></button></form>
    </section><section class="change-detail" aria-labelledby="owner-change-detail-title"><h3 id="owner-change-detail-title" tabindex="-1"></h3>
      <p id="owner-change-detail-stale" role="status" hidden></p><div id="owner-change-detail-body"></div>
    </section></div></details>`;
  mount.append(root);
  const node = id => document.getElementById(id);
  const tr = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const identityKey = identity => JSON.stringify([identity?.issuer, identity?.subject, identity?.actor_id]);
  const identityText = identity => identity ? `${identity.actor_id} · ${identity.subject} · ${identity.issuer}` : "—";
  const statuses = {PENDING_CONFIRMATION:["等待双方确认", "Awaiting both confirmations"], REPLAN_REQUIRED:["已失效，需要重新提案", "Expired; a new proposal is required"], APPLIED:["职责移交已生效", "Owner transfer activated"]};
  let options = null, items = [], selected = null, nextCursor = null, preview = null, previewBody = null;
  let busy = false, optionsCurrent = false, detailCurrent = false, sequence = 0, acknowledgment = null;
  let draft = {slot_id:"", owner_key:"", reason:""}, pendingCreate = null, retryReady = false, registrationRejected = false;
  const sessionIdentity = session => JSON.stringify([session?.mode, session?.authenticated, session?.principal?.tenant_id, identityKey(session?.principal)]);
  let sessionKey = sessionIdentity(client.session?.());
  function notice(message, tone = "info") { node("owner-change-notice").textContent = message; node("owner-change-notice").hidden = !message; node("owner-change-notice").dataset.tone = tone; }
  function reason(code) {
    if (!code) return "";
    const messages = {
      OWNER_CHANGE_POLICY_DISABLED:["本部署尚未启用职责移交政策。可以查看历史；请组织负责人确认政策后由部署管理员启用。", "Owner transfer policy is disabled. History remains available; an organization decision is required before an administrator enables it."],
      OWNER_CHANGE_PROPOSAL_EXPIRED:["提案已过期。请按当前来源和职责重新预览、提案，双方重新确认。", "This proposal expired. Preview and propose against current sources and ownership, then obtain both confirmations again."],
      OWNER_CHANGE_TWO_CONFIRMATIONS_REQUIRED:["仍需现任授权与继任接受，不能由同一身份代替双方。", "Current-owner authorization and successor acceptance are both required, from independent identities."],
      OWNER_CHANGE_PARTICIPANT_REQUIRED:["当前账号不是这次移交的确认人。请由对应负责人使用自己的账号登录。", "This account is not a participant. The responsible person must sign in with their own account."],
      OWNER_CHANGE_POLICY_CHANGED:["组织移交政策已变化，请重新提案。", "The transfer policy changed. Create a new proposal."],
      OWNER_CHANGE_SNAPSHOT_REQUIRED:["请先形成当前工作基线，再预览职责移交。", "Form the current work baseline before previewing an owner transfer."],
      OWNER_CHANGE_ELIGIBLE_OWNER_REQUIRED:["当前没有已核验且具备工作区批准权限的继任者。请组织管理员核对成员授权。", "No verified successor currently has approval access to this workspace. Ask your organization administrator to review membership."],
    };
    const copy = messages[code] || (/^AUTH_/.test(code)
      ? ["身份或工作区权限已不可用。请组织管理员核对授权；历史确认不会代替当前权限。", "Identity or workspace access is unavailable. Ask your organization administrator to review access; historical confirmation does not replace current authority."]
      : /CHANGED|MISMATCH/.test(code)
        ? ["职责、来源或当前工作版本已变化。请刷新后重新预览；旧确认不会转移到新提案。", "Ownership, source, or work version changed. Refresh and preview again; previous confirmations do not transfer to a new proposal."]
        : ["当前条件尚未满足，请刷新核对详情。", "Current conditions are not satisfied. Refresh and review the details."]);
    return `${tr(...copy)} (${code})`;
  }
  function failure(error) {
    if (error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
    notice(tr("请求结果尚未确认。刷新只核对记录，不会自动重发。", "The request result is unconfirmed. Refresh only reads records; no automatic replay occurs.") + " " + reason(error.code || error.message || "UNAVAILABLE"), "error");
  }
  function fact(parent, label, value) {
    const row = document.createElement("div"), term = document.createElement("dt"), description = document.createElement("dd");
    term.textContent = label; description.textContent = value ?? "—"; row.append(term, description); parent.append(row);
  }
  function paragraph(parent, value) { const p = document.createElement("p"); p.textContent = value; parent.append(p); }
  function focusBack(container) {
    const active = document.activeElement;
    if (!active?.id || !container.contains(active)) return () => {};
    const {id, selectionStart, selectionEnd} = active;
    return () => { const replacement = node(id); if (!replacement || !container.contains(replacement)) return; replacement.focus(); if (typeof selectionStart === "number") replacement.setSelectionRange?.(selectionStart, selectionEnd); };
  }
  function resource() { return options?.resources.find(item => item.slot_id === draft.slot_id); }
  function successor() { return options?.eligible_owners.find(item => identityKey(item) === draft.owner_key); }
  function canPreview() { return optionsCurrent && options?.can_propose === true && resource() && successor() && successor().actor_id !== resource().owner_id && draft.reason.trim() && !pendingCreate; }
  function requestBody() { const owner = successor(); return {slot_id:draft.slot_id, expected_binding_digest:options.enterprise_binding_digest, expected_snapshot_digest:options.snapshot_digest, proposed_owner:{issuer:owner.issuer, subject:owner.subject, actor_id:owner.actor_id}, reason:draft.reason.trim()}; }
  function currentPreview() { return Boolean(preview && previewBody && canPreview() && JSON.stringify(previewBody) === JSON.stringify(requestBody())); }
  function permitted(action) { return detailCurrent && !selected?.blocked_reason && selected?.status !== "APPLIED" && selected?.allowed_actions.includes(action); }
  function updateControls() {
    root.setAttribute("aria-busy", String(busy));
    root.querySelectorAll("button").forEach(button => { button.disabled = busy; });
    for (const id of ["owner-change-resource", "owner-change-successor", "owner-change-reason"]) node(id).disabled = busy || Boolean(pendingCreate) || !optionsCurrent || options?.can_propose !== true;
    node("owner-change-preview").disabled = busy || !canPreview();
    node("owner-change-create").hidden = !preview || Boolean(pendingCreate); node("owner-change-create").disabled = busy || !currentPreview();
    node("owner-change-retry").hidden = !pendingCreate; node("owner-change-retry").disabled = busy || !retryReady;
    node("owner-change-restart").hidden = !pendingCreate || !retryReady || !registrationRejected; node("owner-change-restart").disabled = busy || !retryReady || !registrationRejected;
    for (const action of ["CONFIRM_AUTHORIZATION", "CONFIRM_ACCEPTANCE", "ACTIVATE"]) {
      const control = node(`owner-change-action-${action.toLowerCase()}`);
      if (control) control.disabled = busy || !permitted(action) || acknowledgment !== `${selected?.proposal_digest}:${action}`;
    }
    const ack = node("owner-change-ack"); if (ack) ack.disabled = busy || !detailCurrent;
    node("owner-change-detail-stale").hidden = !selected || detailCurrent;
  }
  function renderEditor() {
    const restore = focusBack(node("owner-change-form"));
    for (const [id, values, value, placeholder] of [
      ["owner-change-resource", (options?.resources || []).map(item => ({value:item.slot_id,label:`${item.label || item.slot_id} · ${item.owner_id}`})), draft.slot_id, tr("选择需要移交的职责", "Select the responsibility")],
      ["owner-change-successor", (options?.eligible_owners || []).filter(item => item.actor_id !== resource()?.owner_id).map(item => ({value:identityKey(item),label:identityText(item)})), draft.owner_key, tr("选择已核验的继任者", "Select a verified successor")],
    ]) {
      const select = node(id); select.replaceChildren();
      for (const entry of [{value:"",label:placeholder},...values]) { const option = document.createElement("option"); option.value = entry.value; option.textContent = entry.label; select.append(option); }
      select.value = values.some(entry => entry.value === value) ? value : "";
    }
    node("owner-change-reason").value = draft.reason;
    node("owner-change-policy").textContent = options?.blocked_reason === "AUTH_ACTION_DENIED"
      ? tr("本账号可查看移交并执行属于自己的确认；发起提案需要相应权限。", "This account can review transfers and perform its own confirmations. Proposing a transfer requires the corresponding permission.")
      : options?.blocked_reason ? reason(options.blocked_reason)
      : options?.policy === "mutual-consent-v1" ? tr("本部署启用了双方独立确认政策：现任授权，继任接受，再由其中一方激活。组织仍须自行确认此政策符合本单位授权制度。", "This deployment uses independent mutual consent: current-owner authorization, successor acceptance, then participant activation. Your organization must determine whether this policy meets its authorization rules.")
        : tr("打开面板后读取部署政策与当前职责。", "Open the panel to read deployment policy and current ownership.");
    node("owner-change-account").textContent = client.session?.()?.principal ? tr("当前登录：", "Signed in: ") + identityText(client.session().principal) : tr("正式移交需要已核验的企业身份。", "Formal transfer requires a verified enterprise identity.");
    node("owner-change-draft-context").textContent = pendingCreate
      ? tr("登记结果待核对，原输入已锁定。请刷新查找同一提案；恢复登记也只使用此ID：", "Registration is unconfirmed; original input is locked. Refresh to find the same proposal; recovery reuses this ID: ") + pendingCreate.proposal_id
      : resource() ? tr("当前负责人：", "Current owner: ") + resource().owner_id : "";
    restore();
  }
  function renderImpact(parent, value, frozen = false) {
    const facts = document.createElement("dl");
    fact(facts, tr("负责事项", "Responsibility"), `${value.resource.slot_id} · ${value.resource.object_id}`);
    fact(facts, tr("职责移交", "Owner transfer"), `${value.resource.owner_id} → ${value.proposed_owner.actor_id}`);
    fact(facts, tr("继任者身份", "Successor identity"), identityText(value.proposed_owner));
    fact(facts, tr("移交原因", "Reason"), value.reason);
    fact(facts, tr("来源依据", "Source version"), value.source.ref);
    fact(facts, frozen ? tr("提案形成时间", "Proposal evaluated at") : tr("预览时间", "Preview evaluated at"), value.evaluated_at);
    parent.append(facts);
    paragraph(parent, frozen ? tr("以下是提案创建时冻结的影响范围；确认针对这份提案，激活时后台会再次核对当前条件。", "The following scope was frozen at proposal creation. Confirmation covers this proposal; activation rechecks current conditions.") : tr("本次预览未写入数据库，也不会改变负责人。登记后仍需双方独立确认。", "This preview made no database writes and did not change the owner. Registration still requires both independent confirmations."));
    for (const [title, entries, coverage, describe] of [
      [tr("需要复核的依赖工作", "Dependent work to review"), value.dependent_work, value.dependency_coverage, entry => `${entry.label} · ${entry.ref} · ${entry.state}`],
      [tr("需要重新规划与授权的待处理事件", "Pending events requiring replanning and authorization"), value.pending_events, value.pending_event_coverage, entry => `${entry.event_id} · ${entry.current_status}`],
      [tr("外部操作待核对", "External operations to review"), value.unresolved_effects, value.effect_coverage, entry => `${entry.effect_id} · ${entry.state === "READY" ? tr("尚未发送，需复核原意图", "Not sent; review the original intent") : tr("已认领或结果未知，核查原回执，不重发", "Claimed or unknown; reconcile the original receipt, do not resend")}`],
    ]) {
      const heading = document.createElement("h4"); heading.textContent = `${title} (${entries.length})`; parent.append(heading);
      if (entries.length) { const list = document.createElement("ul"); for (const entry of entries) { const li = document.createElement("li"); li.textContent = describe(entry); list.append(li); } parent.append(list); }
      if (coverage.status === "UNKNOWN") paragraph(parent, tr("范围尚未完整核实，不能据此认为其余事项不存在。", "Coverage is incomplete; do not infer that no other items exist.") + ` ${coverage.reason}`);
      else paragraph(parent, tr("仅覆盖本次已记录范围。", "Covers only the recorded scope."));
    }
    paragraph(parent, tr("外部操作按工作区保守列出，关联尚未确认；移交不会代替旧任务审批，也不会自动重发远端操作。", "External operations are listed conservatively for this workspace; association is unconfirmed. Transfer does not approve old tasks or automatically resend remote operations."));
  }
  function renderPreview() { const parent = node("owner-change-preview-body"); parent.replaceChildren(); if (preview) renderImpact(parent, preview); }
  function renderList() {
    const list = node("owner-change-list"); list.replaceChildren();
    for (const item of items) {
      const p = item.proposal, li = document.createElement("li"), button = document.createElement("button"); button.type = "button";
      button.textContent = `${p.preview.resource.slot_id} · ${p.preview.resource.owner_id} → ${p.preview.proposed_owner.actor_id} · ${statuses[item.status] ? tr(...statuses[item.status]) : item.status}`;
      button.setAttribute("aria-current", String(p.proposal_id === selected?.proposal.proposal_id)); button.addEventListener("click", () => openProposal(p.proposal_id, true)); li.append(button); list.append(li);
    }
    node("owner-change-empty").hidden = items.length > 0; node("owner-change-more").hidden = nextCursor == null;
  }
  function renderDetail() {
    const body = node("owner-change-detail-body"), restore = focusBack(body); body.replaceChildren();
    node("owner-change-detail-title").textContent = selected ? tr(...(statuses[selected.status] || ["状态未核实", "State unverified"])) : tr("选择或打开移交提案", "Select or open a transfer proposal");
    if (!selected) return;
    const proposal = selected.proposal;
    renderImpact(body, proposal.preview, true);
    const facts = document.createElement("dl");
    fact(facts, tr("提案ID", "Proposal ID"), proposal.proposal_id);
    fact(facts, tr("有效期至", "Expires at"), proposal.expires_at);
    fact(facts, tr("现任授权", "Current-owner authorization"), selected.authorization ? identityText(selected.authorization.identity) : tr("等待现任负责人使用自己的账号确认", "Waiting for the current owner to confirm with their own account"));
    fact(facts, tr("继任接受", "Successor acceptance"), selected.acceptance ? identityText(selected.acceptance.identity) : tr("等待继任者使用自己的账号确认", "Waiting for the successor to confirm with their own account"));
    if (selected.activation) fact(facts, tr("生效时间", "Activated at"), selected.activation.activated_at);
    body.append(facts);
    if (selected.blocked_reason) paragraph(body, reason(selected.blocked_reason));
    if (selected.status === "APPLIED") paragraph(body, tr("新的当前负责人已持久保存。旧确认与历史仍保留；受影响的旧任务须重新规划和授权。", "The new current owner is persisted. Earlier confirmations and history remain; affected prior tasks require replanning and authorization."));
    const action = ["CONFIRM_AUTHORIZATION", "CONFIRM_ACCEPTANCE", "ACTIVATE"].find(permitted);
    if (action) {
      const labels = {CONFIRM_AUTHORIZATION:["授权移交我的职责", "Authorize transfer of my responsibility"], CONFIRM_ACCEPTANCE:["接受这项职责", "Accept this responsibility"], ACTIVATE:["激活双方确认的移交", "Activate the jointly confirmed transfer"]};
      const label = document.createElement("label"), ack = document.createElement("input"); ack.type = "checkbox"; ack.id = "owner-change-ack"; ack.checked = acknowledgment === `${selected.proposal_digest}:${action}`; ack.style.width = "auto";
      ack.addEventListener("change", () => { acknowledgment = ack.checked ? `${selected.proposal_digest}:${action}` : null; updateControls(); });
      label.append(ack, document.createTextNode(tr("我已核对双方身份、负责事项、待复核工作与这份提案。", "I reviewed both identities, the responsibility, affected work, and this exact proposal."))); body.append(label);
      const button = document.createElement("button"); button.type = "button"; button.id = `owner-change-action-${action.toLowerCase()}`; button.className = "button button-primary"; button.textContent = tr(...labels[action]); button.addEventListener("click", () => command(action));
      const controls = document.createElement("div"); controls.className = "change-workbench-actions"; controls.append(button); body.append(controls);
    } else if (!selected.blocked_reason && !selected.activation) paragraph(body, tr("本账号当前没有可执行的确认或激活动作。将提案ID交给另一位负责人，由其独立登录后打开。", "This account currently has no confirmation or activation action. Share the proposal ID with the other participant, who must sign in independently to open it."));
    const audit = document.createElement("details"), summary = document.createElement("summary"), pre = document.createElement("pre"); summary.textContent = tr("查看版本与确认记录", "View versions and confirmation records");
    pre.textContent = JSON.stringify({proposal_digest:selected.proposal_digest, binding_digest:proposal.preview.binding_digest, snapshot_digest:proposal.preview.snapshot_digest, source_digest:proposal.preview.source.digest, authorization:selected.authorization, acceptance:selected.acceptance, activation:selected.activation}, null, 2); audit.append(summary, pre); body.append(audit);
    restore();
  }
  function acceptDetail(value, id) {
    if (value?.proposal?.proposal_id !== id || !value.proposal_digest || !Array.isArray(value.allowed_actions)) throw new Error("OWNER_CHANGE_DETAIL_INVALID");
    selected = value; acknowledgment = null; detailCurrent = true;
    items = [value, ...items.filter(item => item.proposal.proposal_id !== id)];
    if (pendingCreate?.proposal_id === id) { pendingCreate = null; retryReady = false; registrationRejected = false; preview = null; previewBody = null; draft = {slot_id:"", owner_key:"", reason:""}; }
  }
  async function openProposal(id, focus = false) {
    if (busy || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(id)) return;
    const turn = ++sequence; busy = true; detailCurrent = false; acknowledgment = null; updateControls();
    try { const value = await client.json(`${base}/${encodeURIComponent(id)}`); if (turn !== sequence) return; acceptDetail(value, id); notice(""); render(); if (focus) node("owner-change-detail-title").focus(); }
    catch (error) { if (turn === sequence) failure(error); }
    finally { if (turn === sequence) { busy = false; updateControls(); } }
  }
  async function refresh(append = false) {
    if (busy) return;
    const turn = ++sequence; busy = true; optionsCurrent = false; detailCurrent = false; acknowledgment = null; retryReady = false; updateControls();
    try {
      const [configuration, page] = await Promise.all([client.json("/api/workspace/organization/owner-change-options"), client.json(`${base}?limit=20${append && nextCursor ? `&after=${encodeURIComponent(nextCursor)}` : ""}`)]);
      if (turn !== sequence) return;
      if (!Array.isArray(configuration.resources) || !Array.isArray(configuration.eligible_owners) || !Array.isArray(page.items)) throw new Error("OWNER_CHANGE_OPTIONS_INVALID");
      if (options?.enterprise_binding_digest !== configuration.enterprise_binding_digest || options?.snapshot_digest !== configuration.snapshot_digest) { preview = null; previewBody = null; }
      options = configuration; optionsCurrent = true; items = append ? [...items, ...page.items.filter(item => !items.some(old => old.proposal.proposal_id === item.proposal.proposal_id))] : page.items; nextCursor = page.next_cursor;
      const id = pendingCreate?.proposal_id || selected?.proposal.proposal_id;
      if (id) {
        try { const detail = await client.json(`${base}/${encodeURIComponent(id)}`); if (turn !== sequence) return; acceptDetail(detail, id); }
        catch (error) { if (turn !== sequence) return; if (pendingCreate?.proposal_id === id && error.status === 404) { retryReady = true; notice(tr("暂未读到原提案。可手动恢复同一ID的登记；输入和身份必须仍相同。", "The original proposal was not found. You may explicitly retry registration with the same ID, input, and identity.")); } else throw error; }
      }
      if (!pendingCreate) notice(""); render();
    } catch (error) { if (turn === sequence) { optionsCurrent = false; detailCurrent = false; failure(error); } }
    finally { if (turn === sequence) { busy = false; updateControls(); } }
  }
  async function getPreview(event) {
    event.preventDefault(); if (busy || !canPreview()) return;
    const body = requestBody(), turn = ++sequence; busy = true; preview = null; previewBody = null; renderPreview(); updateControls();
    try {
      const value = await client.json("/api/workspace/organization/owner-change-preview", {method:"POST",body}); if (turn !== sequence) return;
      if (value.resource.slot_id !== body.slot_id || value.binding_digest !== body.expected_binding_digest || value.snapshot_digest !== body.expected_snapshot_digest || identityKey(value.proposed_owner) !== identityKey(body.proposed_owner) || value.reason !== body.reason || value.activation_allowed !== false || value.database_writes !== 0) throw new Error("OWNER_CHANGE_PREVIEW_BINDING_MISMATCH");
      preview = value; previewBody = body; renderPreview(); notice(tr("影响预览已生成，尚未登记或移交职责。", "Impact preview generated; no proposal was registered and no responsibility was transferred."));
    } catch (error) { if (turn === sequence) { optionsCurrent = false; failure(error); } }
    finally { if (turn === sequence) { busy = false; updateControls(); } }
  }
  async function createProposal(retry = false) {
    if (busy || (retry ? !pendingCreate || !retryReady : !currentPreview())) return;
    pendingCreate ||= {proposal_id:`owner:${crypto.randomUUID()}`, ...previewBody};
    const body = pendingCreate, turn = ++sequence; busy = true; retryReady = false; updateControls();
    try { const value = await client.json(base, {method:"POST",body}); if (turn !== sequence) return; acceptDetail(value, body.proposal_id); node("owner-change-editor").open = false; render(); notice(tr("移交提案已登记。请双方分别登录并核对确认；职责尚未变化。", "Transfer proposal registered. Both participants must sign in separately and confirm; ownership has not changed.")); }
    catch (error) { if (turn === sequence) { registrationRejected = [400,403,409,422].includes(error.status); optionsCurrent = false; detailCurrent = false; renderEditor(); failure(error); } }
    finally { if (turn === sequence) { busy = false; updateControls(); } }
  }
  async function command(action) {
    if (busy || !permitted(action) || acknowledgment !== `${selected.proposal_digest}:${action}`) return;
    const id = selected.proposal.proposal_id, body = {proposal_digest:selected.proposal_digest}, turn = ++sequence;
    busy = true; detailCurrent = false; acknowledgment = null; updateControls();
    try {
      const value = await client.json(`${base}/${encodeURIComponent(id)}/${action === "ACTIVATE" ? "activate" : "confirm"}`, {method:"POST",body}); if (turn !== sequence) return;
      if (value?.proposal_digest !== body.proposal_digest) throw new Error("OWNER_CHANGE_PROPOSAL_DIGEST_MISMATCH");
      acceptDetail(value, id); render(); notice(action === "ACTIVATE" ? tr("请核对服务端生效记录；旧任务仍须按当前职责重新授权。", "Review the server activation record; prior tasks still require authorization under current ownership.") : tr("本人的确认已记录，没有自动激活移交。", "Your confirmation was recorded; transfer was not automatically activated."));
      if (action === "ACTIVATE") { optionsCurrent = false; preview = null; previewBody = null; renderPreview(); window.dispatchEvent(new CustomEvent("orgrebase:workspacechange")); }
    } catch (error) { if (turn === sequence) failure(error); }
    finally { if (turn === sequence) { busy = false; updateControls(); } }
  }
  function render() { renderEditor(); renderPreview(); renderList(); renderDetail(); updateControls(); }
  function clear() {
    ++sequence; options = null; items = []; selected = null; nextCursor = null; preview = null; previewBody = null; pendingCreate = null; retryReady = false; registrationRejected = false; optionsCurrent = false; detailCurrent = false; acknowledgment = null; busy = false; draft = {slot_id:"", owner_key:"", reason:""}; node("owner-change-open-id").value = ""; notice(""); render();
  }
  function translate() {
    const labels = {
      "owner-change-title":["职责移交", "Transfer responsibility"], "owner-change-intro":["先看清谁会接管、哪些工作需要复核，再由现任与继任分别确认，最后激活同一份提案。", "Review who takes over and which work needs review. The current owner and successor confirm independently before activating the same proposal."],
      "owner-change-refresh":["刷新移交记录", "Refresh transfer records"], "owner-change-editor-title":["准备一项职责移交", "Prepare a responsibility transfer"], "owner-change-resource-label":["负责事项", "Responsibility"], "owner-change-successor-label":["继任负责人", "Successor"], "owner-change-reason-label":["移交原因", "Reason for transfer"], "owner-change-preview":["查看影响，不做写入", "Preview impact without writes"], "owner-change-create":["登记提案，交由双方确认", "Register proposal for both participants"], "owner-change-retry":["恢复同一提案的登记", "Retry registration of the same proposal"],
      "owner-change-list-title":["已登记的移交", "Registered transfers"], "owner-change-empty":["本页暂无移交提案。", "No transfer proposals on this page."], "owner-change-more":["加载更多", "Load more"], "owner-change-open-label":["用提案ID重新打开", "Reopen by proposal ID"], "owner-change-open":["打开提案", "Open proposal"], "owner-change-detail-stale":["下面保留上次读取的详情。请刷新确认当前状态后再操作。", "These are the last read details. Refresh to verify current state before acting."],
      "owner-change-restart":["按当前版本重新准备", "Prepare again against current versions"],
    };
    for (const [id, label] of Object.entries(labels)) node(id).textContent = tr(...label);
    render();
  }
  node("owner-change-panel").addEventListener("toggle", () => { if (node("owner-change-panel").open && !optionsCurrent) refresh(); });
  node("owner-change-refresh").addEventListener("click", () => refresh()); node("owner-change-more").addEventListener("click", () => refresh(true));
  node("owner-change-form").addEventListener("submit", getPreview); node("owner-change-create").addEventListener("click", () => createProposal()); node("owner-change-retry").addEventListener("click", () => createProposal(true));
  node("owner-change-restart").addEventListener("click", () => {
    if (busy || !pendingCreate || !registrationRejected || !retryReady) return;
    const id = pendingCreate.proposal_id; pendingCreate = null; registrationRejected = false; retryReady = false; preview = null; previewBody = null;
    render(); notice(tr("登记已被拒绝，刷新也未找到该提案。请按当前版本重新预览。原提案ID：", "Registration was rejected, and refresh found no proposal. Preview again against current versions. Original proposal ID: ") + id);
  });
  node("owner-change-open-form").addEventListener("submit", event => { event.preventDefault(); openProposal(node("owner-change-open-id").value.trim(), true); });
  for (const [id, key, event] of [["owner-change-resource","slot_id","change"],["owner-change-successor","owner_key","change"],["owner-change-reason","reason","input"]]) node(id).addEventListener(event, () => { if (busy || pendingCreate) return; draft[key] = node(id).value; preview = null; previewBody = null; if (key === "slot_id") { draft.owner_key = ""; renderEditor(); } renderPreview(); updateControls(); });
  window.addEventListener("orgrebase:languagechange", translate);
  window.addEventListener("orgrebase:workspacechange", () => { if (node("owner-change-panel").open) refresh(); });
  window.addEventListener("orgrebase:sessionended", clear);
  window.addEventListener("orgrebase:sessionchange", event => { const key = sessionIdentity(event.detail); if (key !== sessionKey) clear(); sessionKey = key; renderEditor(); });
  window.OrgRebaseOwnerChange = Object.freeze({refresh});
  translate();
})();
