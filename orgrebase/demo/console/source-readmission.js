(() => {
  "use strict";
  const client = window.OrgRebaseClient, mount = document.getElementById("task-changes-flow");
  if (!client || !mount) return;
  const root = document.createElement("section"); root.id = "source-readmission"; root.className = "change-workbench";
  root.setAttribute("aria-labelledby", "source-group-title");
  root.innerHTML = `<header class="change-workbench-header"><div><h2 id="source-group-title"></h2><p id="source-group-intro"></p></div><button id="source-group-refresh" class="button button-secondary" type="button"></button></header>
    <p id="source-group-notice" role="status" aria-live="polite" hidden></p>
    <details class="change-editor"><summary id="source-group-editor-title"></summary><form id="source-group-form"><fieldset><legend id="source-group-candidates-label"></legend><div id="source-group-candidates"></div></fieldset>
    <label><span id="source-group-reason-label"></span><textarea id="source-group-reason" required maxlength="1000" rows="3"></textarea></label><button id="source-group-create" type="submit" class="button button-primary"></button></form></details>
    <section id="source-group-attempt" class="change-detail" hidden><h3 id="source-group-attempt-title"></h3><p id="source-group-attempt-id"></p><p id="source-group-attempt-boundary"></p><button id="source-group-check-attempt" type="button" class="button button-secondary"></button><div id="source-group-attempt-summary"></div></section>
    <div class="change-workbench-layout"><section><h3 id="source-group-list-title"></h3><ul id="source-group-list" class="change-event-list"></ul><button id="source-group-more" class="button button-secondary" type="button" hidden></button></section><section class="change-detail"><h3 id="source-group-detail-title" tabindex="-1"></h3><div id="source-group-detail"></div></section></div>`;
  mount.append(root);
  const node = id => document.getElementById(id), tr = (zh,en) => document.documentElement.lang === "en" ? en : zh;
  const label = slot => ({launch_date:tr("上线日期","Launch date"),currency:tr("报价币种","Currency"),product_plan:tr("产品方案","Product plan")})[slot] || slot;
  let candidates = [], items = [], selected = null, cursor = null, chosen = new Set(), draftId = null;
  let available = false, busy = false, sequence = 0, acknowledged = null, reviewTimer = null;
  let failedDraftId = null, attempt = null;
  const statuses = {REVIEW:["等待各负责人审阅","Awaiting each owner's review"],APPROVED:["已全部批准，待统一恢复","All approved; ready to restore"],APPLIED:["已恢复并重建报价","Restored; quote rebuilt"],REJECTED:["恢复组已拒绝，来源仍失效","Group rejected; sources remain invalid"],EXPIRED:["依据或权限已变，须新提案","Base or authority changed; propose again"]};
  function notice(value) { node("source-group-notice").textContent=value; node("source-group-notice").hidden=!value; }
  function failure(error) { notice(tr("请求未确认。请刷新核对结果；不会自动重复提交。","Request not confirmed. Refresh to check the result; no automatic replay occurs.")+` (${error.code || error.message})`); }
  function setBusy(value) { busy=value; root.setAttribute("aria-busy",String(value));root.querySelectorAll("button").forEach(button=>{button.disabled=value || button.dataset.unavailable==="true";}); }
  function button(text, callback, disabled=false) { const element=document.createElement("button"); element.type="button";element.className="button button-secondary";element.textContent=text;element.dataset.unavailable=String(disabled);element.disabled=busy||disabled;element.addEventListener("click",callback);return element; }
  function facts(parent, title, value) { const row=document.createElement("p");row.textContent=`${title}: ${value ?? "—"}`;parent.append(row); }
  function renderCandidates() {
    const container=node("source-group-candidates");container.replaceChildren();
    for(const item of candidates) {
      const line=document.createElement("label"), check=document.createElement("input");check.type="checkbox";check.id=`source-group-choice-${item.event_id}`;check.checked=chosen.has(item.event_id);
      const text=document.createElement("span");text.textContent=`${label(item.slot_id)} · ${item.value} · ${item.owner_id}`;
      check.disabled=Boolean(failedDraftId);
      check.addEventListener("change",()=>{if(failedDraftId)return;if(check.checked)chosen.add(item.event_id);else chosen.delete(item.event_id);draftId=null;updateCreate();});line.append(check,text);container.append(line);
    }
    if(!candidates.length)facts(container,tr("待恢复来源","Sources to restore"),tr("请先在变化编辑器中登记重新确认来源的提案。","First register readmission proposals in the change editor."));
    updateCreate();
  }
  function updateCreate() {
    const selectedCandidates=candidates.filter(item=>chosen.has(item.event_id));
    const valid=!failedDraftId && available && selectedCandidates.length>=2 && selectedCandidates.length<=3 && new Set(selectedCandidates.map(item=>item.slot_id)).size===selectedCandidates.length;
    node("source-group-create").dataset.unavailable=String(!valid);node("source-group-create").disabled=busy||!valid;
    node("source-group-reason").disabled=Boolean(failedDraftId);
  }
  function renderAttempt() {
    node("source-group-attempt").hidden=!failedDraftId;
    node("source-group-attempt-id").textContent=failedDraftId || "";
    const body=node("source-group-attempt-summary");body.replaceChildren();
    if(attempt) {
      if(typeof window.OrgRebaseChangeWorkbench?.renderAdvisoryAttempt==="function")window.OrgRebaseChangeWorkbench.renderAdvisoryAttempt(body,attempt,"source-group-advisory-attempt");
      else facts(body,tr("尝试记录","Attempt record"),tr("诊断组件未加载，暂不显示未经核验的费用或结果。","The diagnostic component did not load; cost and outcome details are unavailable."));
    }
  }
  async function readAttempt(id, turn) {
    const value=await client.json(`/api/workspace/source-readmission-groups/${encodeURIComponent(id)}/attempt`);
    if(turn!==sequence || failedDraftId!==id)return;
    if(value?.group_id!==id || !value.advisory_attempt)throw new Error("SOURCE_GROUP_ATTEMPT_BINDING_MISMATCH");
    attempt=value.advisory_attempt;renderAttempt();
  }
  async function checkAttempt() {
    if(busy || !failedDraftId)return;
    const turn=++sequence,id=failedDraftId;setBusy(true);
    try {await readAttempt(id,turn);}catch(error){if(turn===sequence)failure(error);}
    finally {if(turn===sequence)setBusy(false);}
  }
  function renderList() {
    node("source-group-list").replaceChildren();
    for(const item of items) {const row=document.createElement("li");const control=button(item.group.reason,()=>select(item.group.id));control.setAttribute("aria-pressed",String(selected?.group.id===item.group.id));row.append(control);node("source-group-list").append(row);}
    node("source-group-more").hidden=!cursor;
  }
  function renderDetail() {
    if (reviewTimer !== null) { clearTimeout(reviewTimer); reviewTimer = null; }
    const body=node("source-group-detail");body.replaceChildren();
    node("source-group-detail-title").textContent=selected ? (statuses[selected.state] ? tr(...statuses[selected.state]) : tr("状态待核查","Status unverified")) : tr("选择恢复组","Select a source recovery group");
    if(!selected)return;
    const group=selected.group;
    facts(body,tr("恢复原因","Recovery reason"),group.reason);
    facts(body,tr("共同基线","Common predecessor"),group.predecessor_ref);
    for(const event of selected.source_events || []) {
      const delta=group.change_set?.deltas?.find(item=>item.object_id===event.proposal.id);
      facts(body,label(event.slot_id),`${delta?.base_value ?? "—"} → ${event.proposal.payload.canonical_value} · ${event.owner_id}`);
    }
    const impacts=document.createElement("ul");impacts.className="change-impact-list";
    for(const result of group.preview.results || []) {
      const row=document.createElement("li"),classifications={AFFECTED_HARD:tr("需重建","Rebuild"),UNAFFECTED_WITHIN_DECLARED_BOUNDARY:tr("在证据范围内保留","Preserve within evidence boundary"),UNKNOWN:tr("证据不足，保持待核对","Insufficient evidence; hold for review"),AFFECTED_REVIEW:tr("需要人工复核","Human review required"),AFFECTED_INFORMATIONAL:tr("需核对参考信息","Review supporting information")};
      row.textContent=`${result.object_id} · ${classifications[result.classification] || tr("影响状态未知","Impact status unknown")}`;impacts.append(row);
    }
    if(impacts.children.length)body.append(impacts);
    if(selected.blocked_reason)facts(body,tr("暂不能继续","Cannot continue"),selected.blocked_reason);
    if(selected.rejection)facts(body,tr("拒绝原因","Rejection reason"),selected.rejection.reason);
    if(selected.outcome)facts(body,tr("报价结果","Quote result"),`${selected.outcome.quote.id}@${selected.outcome.quote.version}`);
    if(!["REVIEW","APPROVED","EXPIRED"].includes(selected.state))return;
    if(selected.review_remaining_ms > 0 && selected.state === "REVIEW") {
      facts(body,tr("最早可批准时间","Approval wait"),tr("请继续核对来源后刷新。","Continue reviewing the sources; the view will refresh."));
      reviewTimer=setTimeout(()=>refresh(),Math.min(selected.review_remaining_ms + 50,60000));
    }
    const ackLabel=document.createElement("label"), ack=document.createElement("input");ack.id="source-group-ack";ack.type="checkbox";ack.checked=acknowledged===group.digest;
    const text=document.createElement("span");text.textContent=tr("我已核对全部来源、各自负责人和本次共同基线。","I reviewed every source, its responsible owner, and this exact common predecessor.");ackLabel.append(ack,text);body.append(ackLabel);
    ack.addEventListener("change",()=>{acknowledged=ack.checked?group.digest:null;body.querySelectorAll("button").forEach(control=>{if(control.dataset.approval==="true"){control.dataset.unavailable=String(!ack.checked || selected.review_remaining_ms > 0);control.disabled=busy||!ack.checked || selected.review_remaining_ms > 0;}});ack.focus();});
    for(const owner of selected.owners || []) {
      const box=document.createElement("section");facts(box,tr("负责范围","Owner scope"),`${owner.owner_id} · ${owner.source_ids.join(", ")}`);
      if(owner.source_approval)facts(box,tr("实际批准人","Approved by"),owner.source_approval.approval.actor_id);
      if(owner.allowed_actions?.includes("APPROVE")) {
        const approve=button(tr("批准我的来源范围","Approve my source scope"),()=>command("approve",owner.owner_id),acknowledged!==group.digest || selected.review_remaining_ms > 0);approve.dataset.approval="true";box.append(approve);
      }
      if(owner.allowed_actions?.includes("REJECT")) {
        const reason=document.createElement("textarea");reason.id=`source-group-reject-${owner.owner_id}`;reason.maxLength=1000;reason.rows=2;reason.setAttribute("aria-label",tr("拒绝恢复的原因","Reason for rejecting recovery"));
        box.append(reason,button(tr("拒绝恢复组","Reject recovery group"),()=>{if(reason.value.trim())return command("reject",owner.owner_id,reason.value.trim());reason.focus();}));
      }
      body.append(box);
    }
    if(selected.state==="APPROVED" && selected.allowed_actions?.includes("APPLY"))body.append(button(tr("恢复来源并重建一次报价","Restore sources and rebuild one quote"),()=>command("apply")));
    facts(body,tr("提交边界","Commit boundary"),tr("每位负责人仅批准自己的范围；全部批准后单独应用。任何失败都会保留原来源和报价。","Each owner approves only their source scope. Apply is separate after all approvals; any failure retains the original sources and quote."));
  }
  async function select(id) {
    const turn=++sequence;
    try {const detail=await client.json(`/api/workspace/source-readmission-groups/${encodeURIComponent(id)}`);if(turn!==sequence)return;
      if(detail.schema_version!=="orgrebase.source-readmission-group-detail.v1" || detail.group.id!==id)throw new Error("SOURCE_GROUP_RESPONSE_INVALID");
      if(selected?.group.digest!==detail.group.digest)acknowledged=null;selected=detail;renderList();renderDetail();node("source-group-detail-title").focus();
    }catch(error){if(turn===sequence)failure(error);}
  }
  async function refresh(append=false) {
    const turn=++sequence;
    try {const [options,page]=await Promise.all([client.json("/api/workspace/source-readmission-options"),client.json(`/api/workspace/source-readmission-groups?limit=20${append&&cursor?`&after=${encodeURIComponent(cursor)}`:""}`)]);
      if(turn!==sequence)return;if(options.schema_version!=="orgrebase.source-readmission-options.v1" || !Array.isArray(page.items))throw new Error("SOURCE_GROUP_OPTIONS_INVALID");
      candidates=options.candidates;available=options.allowed_actions.includes("CREATE");items=append?[...items,...page.items]:page.items;cursor=page.next_cursor;renderCandidates();renderList();
      if(failedDraftId) {
        const id=failedDraftId;
        try {
          const detail=await client.json(`/api/workspace/source-readmission-groups/${encodeURIComponent(id)}`);if(turn!==sequence)return;
          if(detail?.schema_version!=="orgrebase.source-readmission-group-detail.v1" || detail.group.id!==id)throw new Error("SOURCE_GROUP_RESPONSE_INVALID");
          selected=detail;failedDraftId=null;attempt=null;draftId=null;chosen.clear();node("source-group-reason").value="";acknowledged=null;renderCandidates();renderList();renderDetail();renderAttempt();
        } catch(error) {if(turn!==sequence)return;if(error.status!==404)throw error;await readAttempt(id,turn);}
      } else if(selected)await select(selected.group.id);else if(items.length)await select(items[0].group.id);else renderDetail();
    }catch(error){if(turn!==sequence)return;if(error.code!=="WORKSPACE_QUOTE_NOT_FORMED" && error.status!==404)failure(error);}
  }
  async function create(event) {
    event.preventDefault();if(busy||node("source-group-create").dataset.unavailable==="true"||!node("source-group-reason").value.trim())return;
    draftId ||= `recovery:${crypto.randomUUID()}`;
    const turn=sequence;
    const body={group_id:draftId,events:candidates.filter(item=>chosen.has(item.event_id)).map(item=>({event_id:item.event_id,event_digest:item.event_digest})),reason:node("source-group-reason").value.trim()};setBusy(true);
    try {const detail=await client.json("/api/workspace/source-readmission-groups",{method:"POST",body});if(turn!==sequence)return;if(detail.group.id!==body.group_id)throw new Error("SOURCE_GROUP_ID_MISMATCH");selected=detail;chosen.clear();draftId=null;node("source-group-reason").value="";acknowledged=null;await refresh();}
    catch(error){if(turn===sequence){failedDraftId=body.group_id;attempt=null;selected=null;acknowledged=null;renderCandidates();renderDetail();renderAttempt();failure(error);}}finally{if(turn===sequence)setBusy(false);}
  }
  async function command(action,owner,reason) {
    if(busy||!selected)return;if(action==="approve"&&acknowledged!==selected.group.digest)return;
    const body={group_digest:selected.group.digest,preview_digest:selected.group.preview.digest};if(owner)body.owner_id=owner;if(reason)body.reason=reason;
    if(owner && client.session?.()?.mode==="local")body.actor_id=owner;
    const id=selected.group.id, turn=sequence;setBusy(true);
    try {await client.json(`/api/workspace/source-readmission-groups/${encodeURIComponent(id)}/${action}`,{method:"POST",body});if(turn!==sequence)return;acknowledged=null;await refresh();window.dispatchEvent(new CustomEvent("orgrebase:workspacechange"));}
    catch(error){failure(error);}finally{setBusy(false);}
  }
  function translate() {
    const copy={"source-group-title":["共同恢复失效来源","Restore invalidated sources together"],"source-group-intro":["两到三项来源同时失效时，共同核对一次影响，各负责人分别批准，最后重建一次报价。","When two or three sources are invalid, review one common impact, obtain each owner's approval, then rebuild one quote."],"source-group-refresh":["刷新恢复状态","Refresh recovery state"],"source-group-editor-title":["建立来源恢复组","Create a source recovery group"],"source-group-candidates-label":["选择不同字段的重新确认提案","Select readmission proposals for distinct fields"],"source-group-reason-label":["共同恢复原因","Reason for joint recovery"],"source-group-create":["生成共同预演","Preview joint recovery"],"source-group-list-title":["来源恢复记录","Source recovery records"],"source-group-more":["加载更多","Load more"]};
    Object.assign(copy,{"source-group-attempt-title":["尚未确认形成恢复组的尝试","Attempt without a confirmed recovery group"],"source-group-check-attempt":["核对此恢复尝试","Check this recovery attempt"],"source-group-attempt-boundary":["保留原尝试ID。核对只读取记录，不会再次调用模型、批准或恢复来源。失败后的新候选须先在变化工作台拒绝原来源提案，再明确修订；不要重发未知结果。","The original attempt ID is retained. Checking only reads records; it does not call the model, approve, or restore sources. For a new candidate after failure, reject the original source proposals in the change workbench and explicitly revise them. Do not resend an unknown result."]});
    for(const [id,text] of Object.entries(copy))node(id).textContent=tr(...text);renderCandidates();renderList();renderDetail();renderAttempt();
  }
  node("source-group-form").addEventListener("submit",create);node("source-group-refresh").addEventListener("click",()=>refresh());node("source-group-more").addEventListener("click",()=>refresh(true));node("source-group-reason").addEventListener("input",()=>{if(!failedDraftId)draftId=null;});node("source-group-check-attempt").addEventListener("click",checkAttempt);
  window.addEventListener("orgrebase:workspacechange",()=>refresh());window.addEventListener("orgrebase:languagechange",translate);
  window.addEventListener("orgrebase:sessionended",()=>{++sequence;items=[];candidates=[];chosen.clear();selected=null;acknowledged=null;available=false;draftId=null;failedDraftId=null;attempt=null;busy=false;node("source-group-reason").value="";renderCandidates();renderList();renderDetail();renderAttempt();});
  window.OrgRebaseSourceReadmission=Object.freeze({refresh,select});translate();refresh();
})();
