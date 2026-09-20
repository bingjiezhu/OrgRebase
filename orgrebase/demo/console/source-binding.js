(() => {
  "use strict";
  const client = window.OrgRebaseClient;
  const mount = document.getElementById("task-changes-flow");
  if (!client || !mount) return;
  const root = document.createElement("section");
  root.id = "source-binding-workbench";
  root.className = "change-workbench";
  root.setAttribute("aria-labelledby", "source-binding-title");
  root.innerHTML = `<details id="source-binding-panel" class="change-editor"><summary id="source-binding-title"></summary>
    <p id="source-binding-intro"></p><button id="source-binding-refresh" class="button button-secondary" type="button"></button>
    <p id="source-binding-notice" class="change-workbench-notice" role="status" aria-live="polite" hidden></p>
    <p id="source-binding-status" role="status"></p><div id="source-binding-gaps"></div>
    <section id="source-binding-coverage" aria-labelledby="source-binding-coverage-title" hidden></section>
    <form id="source-binding-form"><div id="source-binding-fields"></div><button id="source-binding-propose" class="button button-primary" type="submit"></button></form>
    <section aria-labelledby="source-binding-review-title"><h3 id="source-binding-review-title" tabindex="-1"></h3><div id="source-binding-review"></div></section>
  </details>`;
  mount.append(root);
  const node = id => document.getElementById(id);
  const tr = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const slotName = slot => ({launch_date: tr("上线日期", "Launch date"), currency: tr("报价币种", "Currency"), product_plan: tr("产品方案", "Product plan")})[slot] || slot;
  const statusName = status => ({DISCOVERY_REQUIRED: tr("等待管理员读取字段目录", "Waiting for field discovery"), MAPPING_REQUIRED: tr("选择来源与业务字段", "Select source and business fields"), CONFIRMATION_REQUIRED: tr("等待各字段负责人确认", "Waiting for field owners"), CONFIRMED: tr("映射已确认，等待或持续接收来源变化", "Mapping confirmed; source synchronization may proceed")})[status] || tr("状态尚未核实", "State not yet verified");
  let view = null, generation = null, drafts = new Map(), acknowledged = null, sequence = 0, busy = false;
  let coverageExpiryTimer = null;
  let failureCode = null;
  const sessionIdentity = value => value ? JSON.stringify([value.mode, value.authenticated,
    value.principal?.tenant_id, value.principal?.actor_id,
    value.principal?.issuer, value.principal?.subject]) : null;
  let currentSessionIdentity = sessionIdentity(client.session?.());
  function clearSessionView() {
    ++sequence; view = null; generation = null; drafts = new Map(); acknowledged = null; busy = false;
    failureCode = null;
    notice(""); render();
  }
  function notice(message) { node("source-binding-notice").textContent = message; node("source-binding-notice").hidden = !message; }
  function fact(parent, title, value) { const text = document.createElement("p"); text.textContent = `${title}: ${value ?? "—"}`; parent.append(text); }
  function button(label, action, disabled = false) {
    const control = document.createElement("button"); control.type = "button"; control.className = "button button-secondary";
    control.textContent = label; control.dataset.unavailable = String(disabled); control.disabled = busy || disabled;
    control.addEventListener("click", action); return control;
  }
  function fieldLabel(parent, title, control) {
    const label = document.createElement("label"), text = document.createElement("span"); text.textContent = title; label.append(text, control); parent.append(label);
  }
  function select(values, value, title, change) {
    const control = document.createElement("select"); control.setAttribute("aria-label", title);
    for (const item of values) { const option = document.createElement("option"); option.value = item.value; option.textContent = item.label; control.append(option); }
    control.value = value; control.addEventListener("change", () => change(control.value)); return control;
  }
  function can(action) { return !busy && Boolean(view?.allowed_actions.includes(action)); }
  function ready() {
    const selected = [...drafts.values()].filter(item => item.enabled);
    return can("PROPOSE") && selected.length > 0 && selected.every(item => item.record_id && item.field && item.transform);
  }
  function updateControls() {
    root.setAttribute("aria-busy", String(busy));
    root.querySelectorAll("button").forEach(control => { control.disabled = busy || control.dataset.unavailable === "true"; });
    node("source-binding-propose").disabled = !ready();
  }
  function renderFields() {
    const container = node("source-binding-fields"); container.replaceChildren();
    node("source-binding-form").hidden = !view?.inventory;
    for (const candidate of view?.candidates || []) {
      let draft = drafts.get(candidate.slot_id);
      if (!draft) { draft = {slot_id: candidate.slot_id, enabled: false, record_id: view.record_ids.length === 1 ? view.record_ids[0] : "", field: "", transform: "", pairs: []}; drafts.set(candidate.slot_id, draft); }
      const box = document.createElement("fieldset"), legend = document.createElement("legend"); legend.textContent = slotName(candidate.slot_id); box.append(legend);
      const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.id = `source-binding-use-${candidate.slot_id}`; enabled.checked = draft.enabled;
      enabled.disabled = !view.allowed_actions.includes("PROPOSE") || !candidate.owner_available;
      enabled.addEventListener("change", () => { draft.enabled = enabled.checked; updateControls(); });
      fieldLabel(box, tr("接入这个业务字段", "Connect this business field"), enabled);
      fact(box, tr("负责确认", "Responsible owner"), candidate.owner_id);
      if (!candidate.owner_available) fact(box, tr("需要处理", "Action needed"), tr("负责人尚未获得当前工作区的批准权限。", "The owner does not currently have approval access to this workspace."));
      const grid = document.createElement("div"); grid.className = "change-form-grid";
      const record = select([{value: "", label: tr("请选择记录", "Select a record")}, ...view.record_ids.map(id => ({value: id, label: id}))], draft.record_id, tr("来源记录", "Source record"), value => { draft.record_id = value; updateControls(); });
      record.id = `source-binding-record-${candidate.slot_id}`; fieldLabel(grid, tr("来源记录", "Source record"), record);
      const suggested = new Set(candidate.fields.map(item => item.field));
      const fields = view.inventory.fields.filter(item => item.transforms.length);
      const field = select([{value: "", label: tr("请选择字段", "Select a field")}, ...fields.map(item => ({value: item.field, label: `${item.label} (${item.field})${suggested.has(item.field) ? tr(" · 名称匹配候选", " · name-match candidate") : ""}`}))], draft.field, tr("来源字段", "Source field"), value => {
        draft.field = value; draft.transform = fields.find(item => item.field === value)?.transforms[0] || ""; renderFields(); node(`source-binding-field-${candidate.slot_id}`).focus();
      });
      field.id = `source-binding-field-${candidate.slot_id}`; fieldLabel(grid, tr("来源字段", "Source field"), field);
      const current = fields.find(item => item.field === draft.field);
      const transform = select((current?.transforms || []).map(value => ({value, label: value === "date_only" ? tr("仅保留日期", "Use calendar date") : tr("原值对应", "Use source value")})), draft.transform, tr("值的处理", "Value handling"), value => { draft.transform = value; updateControls(); });
      transform.id = `source-binding-transform-${candidate.slot_id}`; fieldLabel(grid, tr("值的处理", "Value handling"), transform); box.append(grid);
      const mapping = document.createElement("details"), summary = document.createElement("summary"); summary.textContent = tr("需要时设置值的对应关系", "Map source values when needed"); mapping.append(summary);
      mapping.open = draft.pairs.length > 0;
      for (const [index, pair] of draft.pairs.entries()) {
        const row = document.createElement("div"); row.className = "change-form-grid";
        for (const [key, title, length] of [["from", tr("处理后的来源值", "Processed source value"), 256], ["to", tr("业务值", "Business value"), 2000]]) {
          const input = document.createElement("input"); input.value = pair[key]; input.maxLength = length;
          input.id = `source-binding-value-${candidate.slot_id}-${index}-${key}`;
          input.addEventListener("input", () => { pair[key] = input.value; }); fieldLabel(row, title, input);
        }
        row.append(button(tr("删除对应", "Remove mapping"), () => { draft.pairs.splice(index, 1); renderFields(); node(`source-binding-field-${candidate.slot_id}`).focus(); })); mapping.append(row);
      }
      mapping.append(button(tr("增加值对应", "Add value mapping"), () => { draft.pairs.push({from: "", to: ""}); renderFields(); node(`source-binding-value-${candidate.slot_id}-${draft.pairs.length - 1}-from`).focus(); }, draft.pairs.length >= 128));
      box.append(mapping); container.append(box);
    }
    updateControls();
  }
  function renderReview() {
    const container = node("source-binding-review"); container.replaceChildren();
    node("source-binding-review-title").textContent = tr("核对具体映射后确认", "Review the exact mapping before confirming");
    const proposal = view?.proposal;
    if (!proposal) { fact(container, tr("当前提案", "Current proposal"), tr("尚无可确认的映射。", "No mapping is ready for confirmation.")); return; }
    for (const item of proposal.mappings) {
      const box = document.createElement("section");
      fact(box, slotName(item.slot_id), `${item.record_id} · ${item.field} → ${item.transform === "date_only" ? tr("仅日期", "Date only") : tr("原值", "Source value")}`);
      fact(box, tr("负责确认", "Responsible owner"), item.owner_id);
      for (const [source, target] of Object.entries(item.value_map)) fact(box, source, target);
      container.append(box);
    }
    for (const decision of proposal.decisions) fact(container, decision.owner_id, ({PENDING: tr("待确认", "Pending"), CONFIRMED: tr("已确认", "Confirmed"), REVOKED: tr("已撤销，请新提案", "Revoked; propose again"), AUTHORITY_REVOKED: tr("权限已撤销", "Authority revoked")})[decision.status] || decision.status);
    if (view.allowed_actions.includes("CONFIRM")) {
      const ack = document.createElement("input"); ack.type = "checkbox"; ack.id = "source-binding-ack"; ack.checked = acknowledged === proposal.proposal_digest;
      const confirm = button(tr("确认我负责的字段映射", "Confirm my field mappings"), () => decide("confirm"), !ack.checked);
      ack.addEventListener("change", () => { acknowledged = ack.checked ? proposal.proposal_digest : null; confirm.dataset.unavailable = String(!ack.checked); updateControls(); });
      fieldLabel(container, tr("我已核对记录、字段、处理方式和值对应；仅确认我的负责范围。", "I reviewed the record, field, value handling and mappings; I confirm only my scope."), ack); container.append(confirm);
    }
    if (view.allowed_actions.includes("REVOKE")) container.append(button(tr("撤销我对该映射的确认", "Revoke my mapping confirmation"), () => decide("revoke")));
  }
  function coverageReason(code) {
    const reasons = {
      SOURCE_DISCOVERY_REQUIRED: ["尚未读取来源字段目录", "Field discovery has not been recorded", "请管理员先读取字段目录，再选择映射并由负责人确认。", "Ask an administrator to discover fields, then select mappings for owner confirmation."],
      SOURCE_BASELINE_NOT_OBSERVED: ["尚无已提交的首次同步", "No initial synchronization has been committed", "完成映射确认和工作区基线后，请管理员运行首次同步。", "After mapping confirmation and workspace baseline formation, ask an administrator to run the initial synchronization."],
      SOURCE_PAGINATION_INCOMPLETE: ["来源分页尚未读取完", "Source pagination is incomplete", "请管理员继续现有同步，完成剩余分页后刷新。", "Ask an administrator to continue synchronization and refresh after all pages are processed."],
      SOURCE_RECORDS_NOT_OBSERVED: ["部分范围内记录尚未观察到", "Some scoped records have not been observed", "请管理员核对记录范围和读取权限，并完成同步；未读到不代表记录不存在。", "Ask an administrator to check record scope and read access, then complete synchronization. Unobserved records are not proven absent."],
      SOURCE_CURRENT_READBACK_REQUIRED: ["当前记录回读尚未完成", "Current-record readback is incomplete", "请管理员完成所选记录的回读并刷新；不要按缺失记录继续处理。", "Ask an administrator to complete readback of the selected records and refresh; do not treat them as missing."],
      SOURCE_COVERAGE_EXPIRED: ["来源读取证据已过期", "Source read evidence has expired", "请管理员重新同步来源，再刷新核对新的有效期。", "Ask an administrator to synchronize the source again, then refresh to check the new validity window."],
      SOURCE_INVENTORY_FRESHNESS_UNKNOWN: ["字段目录时效尚未确认", "Field-discovery freshness is unverified", "请管理员重新读取字段目录，再核对映射和负责人确认。", "Ask an administrator to rediscover fields, then review the mappings and owner confirmations."],
      SOURCE_MAPPING_CONFIRMATION_REQUIRED: ["映射缺少全部有效确认", "The mapping lacks all valid confirmations", "请核对下方负责人状态，由各负责人确认自己的范围。", "Review the owner statuses below; each owner must confirm their own scope."],
      SOURCE_MAPPING_RECONFIRMATION_REQUIRED: ["来源或映射世代已变化", "The source or mapping generation changed", "请按最新字段目录提交新映射，由各负责人重新确认。", "Submit a new mapping against the latest field discovery and obtain new owner confirmations."],
      SOURCE_PERMISSION_DENIED: ["来源读取权限不足", "Source read access was denied", "请管理员核对源账号的记录与字段读取权限，恢复后重新同步。", "Ask an administrator to check the source account's record and field read access, then synchronize again."],
      SOURCE_AUTHENTICATION_FAILED: ["来源身份验证失败", "Source authentication failed", "请管理员更新来源只读凭据并重新同步；不要在页面输入令牌。", "Ask an administrator to renew the source read credentials and synchronize again; do not enter tokens on this page."],
      SOURCE_CURSOR_EXPIRED: ["来源增量游标已过期", "The source delta cursor has expired", "请管理员核对来源保留窗口与恢复范围后处理，再刷新接入状态。", "Ask an administrator to review the source retention window and recovery scope, then refresh the connection state."],
      SOURCE_SCHEMA_DRIFT: ["来源字段结构已变化", "The source field schema changed", "请管理员重新读取字段目录并核对映射；需要变更时重新取得负责人确认。", "Ask an administrator to rediscover fields and review mappings; changed mappings need new owner confirmation."],
      SOURCE_OWNER_MEMBERSHIP_MISSING: ["负责人缺少当前工作区的批准权限", "The owner lacks approval access to this workspace", "请管理员核对负责人身份、角色和工作区范围。", "Ask an administrator to check the owner's identity, role, and workspace scope."],
      SOURCE_COVERAGE_BINDING_MISMATCH: ["读取证据与当前映射绑定不一致", "Read evidence does not match the current mapping", "请管理员核对当前映射与同步世代，完成正确范围的同步后刷新。", "Ask an administrator to check the mapping and synchronization generation, then synchronize the correct scope and refresh."],
      SOURCE_COVERAGE_UNAVAILABLE: ["最新同步未提供有效读取证据", "The latest synchronization has no valid read evidence", "请管理员检查最近一次来源同步的结果，恢复后重新同步并刷新。", "Ask an administrator to check the latest source synchronization result, then synchronize again and refresh after recovery."],
      SOURCE_CONFIG_ABSENT: ["尚未配置企业来源", "Enterprise source is not configured", "请管理员按来源接入指南配置只读连接，再刷新。已准入工作仍可继续处理。", "Ask an administrator to configure a read-only connection using the source onboarding guide, then refresh. Admitted work remains available."],
      SOURCE_CONFIG_INVALID: ["来源接入配置不可用", "Source configuration is unavailable", "请管理员检查当前工作区的来源配置，再刷新接入状态。", "Ask an administrator to check this workspace's source configuration, then refresh the connection state."],
    };
    const value = Object.prototype.hasOwnProperty.call(reasons, code) ? reasons[code] : [
      "来源读取证据尚不能确认", "Source read evidence is unverified",
      "请管理员核对最新同步、访问权限与来源映射，再刷新；不要据此判断记录不存在。", "Ask an administrator to check synchronization, access, and source mappings, then refresh; do not infer that records are absent.",
    ];
    return [tr(value[0], value[1]), tr(value[2], value[3])];
  }
  function coverageTime(value) {
    if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value)) return null;
    const result = Date.parse(value);
    return Number.isFinite(result) ? result : null;
  }
  function renderCoverage() {
    window.clearTimeout?.(coverageExpiryTimer); coverageExpiryTimer = null;
    const container = node("source-binding-coverage"); container.replaceChildren(); container.hidden = !view;
    if (!view) return;
    const title = document.createElement("h3"); title.id = "source-binding-coverage-title";
    title.textContent = tr("来源读取覆盖", "Source read coverage"); container.append(title);
    const coverage = view.coverage;
    const observed = coverageTime(coverage?.observed_at), expires = coverageTime(coverage?.expires_at);
    const records = Array.isArray(coverage?.record_ids)
      && coverage.record_ids.every(id => typeof id === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id)) ? coverage.record_ids : [];
    const fields = Array.isArray(coverage?.fields)
      && coverage.fields.every(field => typeof field === "string" && /^[a-z][a-z0-9_]{0,127}$/.test(field)) ? coverage.fields : [];
    const reasonsValid = Array.isArray(coverage?.reasons) && coverage.reasons.every(code => typeof code === "string");
    const reasons = Array.isArray(coverage?.reasons) ? coverage.reasons.filter(code => typeof code === "string") : [];
    const expired = expires !== null && expires <= Date.now();
    if (expired) reasons.push("SOURCE_COVERAGE_EXPIRED");
    const complete = coverage?.schema_version === "orgrebase.source-coverage.v1" && coverage.status === "COMPLETE"
      && reasonsValid && !reasons.length && records.length > 0 && fields.length > 0 && observed !== null && expires > observed && !expired;
    container.dataset.status = complete ? "COMPLETE" : "UNKNOWN";
    fact(container, tr("读取状态", "Read status"), complete
      ? tr("所列范围读取完整，仍受有效期限制", "Complete for the listed scope, within its validity window")
      : tr("覆盖尚不能确认，未读到不代表不存在", "Coverage is unverified; unobserved does not mean absent"));
    const scope = document.createElement("details"), summary = document.createElement("summary");
    summary.textContent = records.length ? tr(`已记录范围：${records.length} 条记录 · ${fields.length} 个字段`, `Recorded scope: ${records.length} ${records.length === 1 ? "record" : "records"} · ${fields.length} ${fields.length === 1 ? "field" : "fields"}`) : tr("尚无可核对的已提交范围", "No committed scope is available for review");
    scope.append(summary);
    records.slice(0, 32).forEach(id => fact(scope, tr("记录", "Record"), id));
    if (records.length > 32) fact(scope, tr("显示范围", "Displayed scope"), tr(`仅展开前 32 条记录，共 ${records.length} 条。`, `Showing the first 32 of ${records.length} records.`));
    fact(scope, tr("来源字段", "Source fields"), fields.length ? fields.slice(0, 32).join(" · ") : tr("尚未记录", "Not recorded"));
    if (fields.length > 32) fact(scope, tr("显示范围", "Displayed scope"), tr(`仅展开前 32 个字段，共 ${fields.length} 个。`, `Showing the first 32 of ${fields.length} fields.`));
    container.append(scope);
    const timestamp = value => value === null ? tr("尚未记录", "Not recorded") : new Date(value).toISOString().replace("T", " ").replace(".000Z", " UTC").replace("Z", " UTC");
    fact(container, tr("观察时间", "Observed at"), timestamp(observed));
    fact(container, tr("有效期至", "Valid until"), timestamp(expires));
    if (!complete) {
      if (!reasons.length) reasons.push(coverage ? "UNKNOWN" : view.status === "DISCOVERY_REQUIRED" ? "SOURCE_DISCOVERY_REQUIRED" : "SOURCE_BASELINE_NOT_OBSERVED");
      const explanations = new Set();
      for (const code of reasons) {
        const [reason, action] = coverageReason(code), message = `${reason}${tr("。", ". ")}${action}`;
        if (!explanations.has(message)) { fact(container, tr("下一步", "Next step"), message); explanations.add(message); }
      }
    } else if (typeof window.setTimeout === "function") {
      coverageExpiryTimer = window.setTimeout(renderCoverage, Math.min(expires - Date.now() + 1, 2_147_483_647));
    }
    fact(container, tr("办理提示", "Workflow"), tr("映射确认与来源同步分别检查；生效操作仍需业务批准。", "Mapping confirmation and source synchronization are checked separately; applying changes still requires business approval."));
  }
  function render() {
    node("source-binding-status").textContent = failureCode ? coverageReason(failureCode)[0] : statusName(view?.status);
    if (failureCode) {
      const [reason, action] = coverageReason(failureCode);
      notice(`${reason}${tr("。", ". ")}${action} ${tr("操作不会自动重发。", "No operation is replayed.")}`);
    }
    const gaps = node("source-binding-gaps"); gaps.replaceChildren();
    for (const gap of view?.gaps || []) {
      const reason = gap.code === "SOURCE_OWNER_MEMBERSHIP_MISSING" ? tr("请管理员为负责人配置当前工作区的批准权限。", "An administrator must grant the owner approval access to this workspace.")
        : gap.code === "SOURCE_SLOT_TYPE_UNSUPPORTED" ? tr("此连接器只同步单字段文本，尚不支持明细或计算规则。请在变化工作台提交有来源的结构化提案。", "This connector synchronizes scalar text fields; line items and calculation rules require a source-backed structured proposal in the change workbench.")
        : /FRESHNESS|EXPIRED/.test(gap.code) ? tr("来源目录或读取证据已过期，请管理员重新读取。", "Source discovery or read evidence expired; an administrator must refresh it.")
          : /RECONFIRMATION|CHANGED/.test(gap.code) ? tr("来源或字段含义已变化，请提交新的映射并重新确认。", "The source or field meaning changed; submit and confirm a new mapping.")
            : /CONFIRMATION|REVOKED/.test(gap.code) ? tr("映射尚未获得全部有效确认，请核对下方负责人状态。", "The mapping lacks all current confirmations; review the owner decisions below.")
              : tr("来源读取尚不能确认，请管理员检查最新同步与访问权限。", "Source reads are not yet verified; an administrator must check synchronization and access.");
      fact(gaps, gap.slot_id ? slotName(gap.slot_id) : tr("待处理", "Needs attention"), reason);
    }
    renderCoverage();
    renderFields(); renderReview(); updateControls();
  }
  function accept(value) {
    if (value?.schema_version !== "orgrebase.source-binding-view.v1" || !Array.isArray(value.allowed_actions) || !Array.isArray(value.candidates) || !Array.isArray(value.record_ids)) throw new Error("SOURCE_BINDING_VIEW_INVALID");
    if (generation !== value.generation_digest) { drafts = new Map(); acknowledged = null; generation = value.generation_digest; }
    if (view?.proposal?.proposal_digest !== value.proposal?.proposal_digest) acknowledged = null;
    failureCode = null; view = value; render();
  }
  function fail(error) {
    failureCode = typeof error?.code === "string" ? error.code : "UNKNOWN";
    view = null; acknowledged = null; render();
  }
  async function refresh() {
    if (busy) return;
    const turn = ++sequence;
    try { const value = await client.json("/api/workspace/source-binding"); if (turn !== sequence) return; accept(value); notice(""); }
    catch (error) { if (turn === sequence) fail(error); }
  }
  async function send(path, body) {
    if (busy) return;
    const turn = ++sequence; busy = true; updateControls();
    try { const value = await client.json(path, {method: "POST", body}); if (turn !== sequence) return; accept(value); notice(tr("已记录，请核对当前状态。", "Recorded. Review the current state.")); }
    catch (error) { if (turn === sequence) fail(error); }
    finally { if (turn === sequence) { busy = false; updateControls(); } }
  }
  async function propose(event) {
    event.preventDefault(); if (!ready()) return;
    const mappings = [];
    for (const item of drafts.values()) {
      if (!item.enabled) continue;
      const valueMap = Object.create(null);
      for (const pair of item.pairs) {
        if (!pair.from || !pair.to || Object.prototype.hasOwnProperty.call(valueMap, pair.from)) { notice(tr("值对应需要两侧都有值，且来源值不能重复。", "Each mapping needs both values; source values cannot repeat.")); return; }
        valueMap[pair.from] = pair.to;
      }
      mappings.push({slot_id: item.slot_id, record_id: item.record_id, field: item.field, transform: item.transform, value_map: valueMap});
    }
    await send("/api/workspace/source-binding/proposals", {inventory_digest: view.inventory_digest, generation_digest: view.generation_digest, mappings});
  }
  async function decide(action) {
    if (!can(action === "confirm" ? "CONFIRM" : "REVOKE") || !view?.proposal) return;
    const digest = view.proposal.proposal_digest;
    if (action === "confirm" && acknowledged !== digest) return;
    await send(`/api/workspace/source-binding/proposals/${encodeURIComponent(digest.slice(7))}/${action}`, {proposal_digest: digest});
  }
  function translate() {
    const labels = {"source-binding-title": ["企业来源接入", "Connect enterprise sources"], "source-binding-intro": ["管理员先读取字段目录；选择具体记录与字段，由各负责人确认含义，随后后台接收变化。名称相似只提供候选。", "An administrator discovers the fields. Select exact records and fields, then each owner confirms their meaning before background synchronization. Name matches are only suggestions."], "source-binding-refresh": ["刷新接入状态", "Refresh connection state"], "source-binding-propose": ["提交字段映射供负责人核对", "Submit mappings for owner review"]};
    for (const [id, label] of Object.entries(labels)) node(id).textContent = tr(...label);
    render();
  }
  node("source-binding-panel").addEventListener("toggle", () => { if (node("source-binding-panel").open && !view) refresh(); });
  node("source-binding-refresh").addEventListener("click", refresh);
  node("source-binding-form").addEventListener("submit", propose);
  window.addEventListener("orgrebase:workspacechange", () => { if (node("source-binding-panel").open) refresh(); });
  window.addEventListener("orgrebase:languagechange", translate);
  window.addEventListener("orgrebase:sessionchange", event => {
    const next = sessionIdentity(event.detail);
    if (next === currentSessionIdentity) return;
    currentSessionIdentity = next;
    clearSessionView();
    if (node("source-binding-panel").open && event.detail
      && (!event.detail.authentication_required || event.detail.authenticated)) refresh();
  });
  window.addEventListener("orgrebase:sessionended", clearSessionView);
  window.OrgRebaseSourceBinding = Object.freeze({refresh});
  translate();
})();
