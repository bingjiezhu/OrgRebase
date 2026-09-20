(() => {
  "use strict";
  const tr = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const records = value => Array.isArray(value) ? value : [];
  const title = item => document.documentElement.lang === "en" ? item.label_en || item.ref || item.executor_id : item.label || item.ref || item.executor_id;
  const statuses = {
    NONE: ["补充审阅依据", "Request supporting evidence"],
    NEEDS_EVIDENCE: ["等待补证", "Awaiting evidence"],
    RESUMING: ["补证任务执行中", "Evidence task in progress"],
    FAILED: ["本轮执行未完成", "This attempt did not complete"],
    READY_FOR_REVIEW: ["新候选待复核", "New candidate awaiting review"],
  };
  function node(tag, text, id) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (id) element.id = id;
    return element;
  }
  function field(form, caption, control) {
    const label = node("label"), text = node("span", caption);
    label.append(text, control); form.append(label); return control;
  }
  function fact(list, label, value) {
    const group = node("div"); group.append(node("dt", label), node("dd", value == null ? "—" : String(value))); list.append(group);
  }
  function choices(select, options, selected) {
    select.replaceChildren();
    for (const item of options) {
      const option = node("option", item.label); option.value = item.value; select.append(option);
    }
    select.value = options.some(item => item.value === selected) ? selected : options[0]?.value || "";
  }
  function render(container, detail, { busy = false, isBusy = () => busy, draft = {}, onDraft, onCommand } = {}) {
    const recovery = detail.recovery;
    if (!recovery) return;
    const section = node("section", undefined, "change-evidence-recovery"); section.className = "change-evidence-recovery";
    section.append(node("h3", tr("补证与任务继续", "Evidence and task continuation")));
    container.append(section);
    if (!Object.hasOwn(statuses, recovery.state) || !recovery.context_digest
      || recovery.preserved_context?.event_id !== detail.event?.event_id) {
      section.append(node("p", tr("无法核对本次变更的补证记录，请刷新后继续。", "The evidence record could not be bound to this change. Refresh before continuing.")));
      return;
    }
    section.dataset.state = recovery.state;
    const actions = records(recovery.allowed_actions);
    const reviewer = detail.active_owner_id || recovery.owner_id;
    if (recovery.state === "NONE" && !actions.length) { section.hidden = true; return; }
    const terminalStatus = detail.status === "APPLIED" ? tr("补证已随变更生效", "Evidence included in the applied change")
      : detail.status === "APPROVED" ? tr("补证已纳入批准", "Evidence included in the approval")
        : detail.status === "REJECTED" ? tr("变更已拒绝，补证记录保留", "Change rejected; evidence records preserved") : null;
    const status = node("p", terminalStatus || tr(...statuses[recovery.state]), "change-recovery-status"); status.setAttribute("role", "status"); section.append(status);
    const facts = node("dl");
    fact(facts, tr("负责复核", "Responsible reviewer"), reviewer);
    if (recovery.executor) fact(facts, tr("本轮执行者", "Executor for this round"), title(recovery.executor));
    if (recovery.requested_by) fact(facts, tr("退回人", "Returned by"), recovery.requested_by);
    if (recovery.submitted_by) fact(facts, tr("补证提交人", "Evidence supplied by"), recovery.submitted_by);
    if (recovery.round) fact(facts, tr("补证轮次", "Evidence round"), recovery.round);
    if (recovery.reason) fact(facts, tr("待解决问题", "Issue to resolve"), recovery.reason);
    if (recovery.task_id) fact(facts, tr("需要继续的任务", "Task to continue"), recovery.task_id);
    section.append(facts);
    const next = detail.status === "APPLIED"
      ? tr("本轮依据已关联到生效记录，可展开查看上下文和任务历史。", "This round's evidence is linked to the committed result. Expand the context and task history for details.")
      : detail.status === "APPROVED"
        ? tr("等待有执行权限的成员应用这份已批准提案。", "An authorized executor can apply this approved proposal.")
        : detail.status === "REJECTED"
          ? tr("本提案不再继续执行，已有任务和补证记录可供追溯。", "This proposal will not continue. Its task and evidence records remain available for review.")
          : recovery.state === "NEEDS_EVIDENCE"
      ? tr("有提案权限的成员补齐指定依据，并选择已准入的执行能力继续本任务。", "A member with proposal permission supplies the requested evidence and chooses an admitted executor to continue this task.")
      : recovery.execution_uncertain
        ? tr("本轮执行回执尚未确认，请核查原任务记录后继续，当前不能重新委派。", "This attempt's execution receipt is unconfirmed. Check the original task record before continuing; it cannot be delegated again yet.")
      : recovery.state === "RESUMING"
        ? tr("正在重新委派任务。刷新可查看进度；完成后仍需复核与人工批准。", "The task is being delegated again. Refresh to view progress; review and human approval are still required.")
        : recovery.state === "FAILED"
          ? tr(`请 ${reviewer} 核对本轮失败记录，需要继续时重新发起补证。`, `${reviewer} should review this failed attempt and request a new evidence round if needed.`)
          : recovery.state === "READY_FOR_REVIEW"
            ? tr(`请 ${reviewer} 审阅新候选与影响范围，再作批准或拒绝决定。`, `${reviewer} should review the new candidate and effects before approving or rejecting it.`)
            : tr("指定一项任务及缺少的依据，交由有提案权限的成员补证。", "Identify the task and missing evidence for a member with proposal permission to supply.");
    section.append(node("p", next, "change-recovery-next"));
    if (recovery.state !== "NONE") {
      const retained = node("details", undefined, "change-recovery-context");
      retained.append(node("summary", tr("保留的上下文与历史", "Preserved context and history")));
      const context = node("dl");
      for (const [key, zh, en] of [
        ["event_id", "变更提案", "Change proposal"], ["change_set_digest", "变更集", "ChangeSet"],
        ["preview_digest", "影响预演", "Impact preview"], ["previous_preview_artifact_digest", "前次预演记录", "Previous preview record"],
        ["previous_native_receipt_digest", "前次任务回执", "Previous task receipt"],
      ]) if (recovery.preserved_context[key]) fact(context, tr(zh, en), recovery.preserved_context[key]);
      fact(context, tr("本轮补证记录", "Current evidence record"), recovery.recovery_digest);
      retained.append(context);
      if (records(recovery.history).length) {
        const history = node("ol");
        for (const item of recovery.history) {
          const row = node("li");
          row.textContent = [item.round ? tr(`第 ${item.round} 轮`, `Round ${item.round}`) : null,
            statuses[item.state] ? tr(...statuses[item.state]) : item.resume_digest ? tr("已提交补证", "Evidence supplied") : tr("退回记录", "Evidence requested"),
            item.requested_by ? tr(`退回人：${item.requested_by}`, `Returned by: ${item.requested_by}`) : item.actor_id,
            item.reason].filter(Boolean).join(" · ");
          history.append(row);
        }
        retained.append(history);
      }
      if (records(recovery.consumed_evidence).length) {
        const supplied = node("dl");
        for (const item of recovery.consumed_evidence) fact(supplied, tr("本轮使用的依据", "Evidence used in this round"), `${item.ref} · ${item.digest}`);
        retained.append(supplied);
      }
      section.append(retained);
    }
    const returning = actions.includes("RETURN_FOR_EVIDENCE"), resuming = actions.includes("RESUME");
    if (!returning && !resuming) return;
    const action = returning ? "RETURN_FOR_EVIDENCE" : "RESUME";
    const form = node("form", undefined, "change-recovery-form"); form.className = "change-decision-form";
    const task = node("select", undefined, "change-recovery-task"); task.required = true;
    const tasks = records(recovery.tasks);
    choices(task, tasks.map(item => ({value: item.task_id, label: `${item.actor_id} · ${item.task_id}`})), returning ? draft.task_id : recovery.task_id);
    if (returning) field(form, tr("需要补证的任务", "Task requiring evidence"), task);
    else task.value = recovery.task_id || "";
    const reason = node("textarea", undefined, "change-recovery-reason"); reason.required = true; reason.rows = 3; reason.maxLength = 1000; reason.value = draft.reason || "";
    if (returning) field(form, tr("需要补齐什么，为什么", "What is missing and why"), reason);
    const executor = node("select", undefined, "change-recovery-executor"); executor.required = true;
    if (resuming) field(form, tr("继续执行的能力", "Executor for continuation"), executor);
    const evidenceGroup = node("fieldset"); evidenceGroup.className = "change-recovery-evidence";
    evidenceGroup.append(node("legend", returning ? tr("要求补齐的依据", "Required evidence") : tr("本次提交的依据", "Evidence supplied now")));
    const evidenceList = node("div", undefined, "change-recovery-evidence-list"); evidenceGroup.append(evidenceList); form.append(evidenceGroup);
    const message = node("p", "", "change-recovery-validation"); message.setAttribute("role", "status"); form.append(message);
    const submit = node("button", returning ? tr("退回补证", "Return for evidence") : tr("补证并继续任务", "Supply evidence and continue"), "change-recovery-submit");
    submit.type = "submit"; submit.className = "button button-secondary"; form.append(submit);
    let evidenceControls = [], availableEvidence = [];
    const selectedRefs = () => evidenceControls.filter(item => item.control.checked).map(item => item.evidence.ref);
    function remember() {
      onDraft?.({task_id: task.value, reason: reason.value, executor_id: executor.value, evidence_refs: selectedRefs()});
      validate();
    }
    function validate() {
      const selectedTask = tasks.find(item => item.task_id === task.value);
      const refs = selectedRefs();
      const required = resuming ? records(recovery.required_evidence_refs) : [];
      const missing = required.filter(ref => !availableEvidence.some(item => item.ref === ref));
      const valid = Boolean(selectedTask && refs.length && (!returning || reason.value.trim())
        && (!resuming || selectedTask.allowed_executors?.some(item => item.executor_id === executor.value))
        && required.every(ref => refs.includes(ref)) && (!resuming || refs.length === required.length));
      submit.dataset.unavailable = String(!valid); submit.disabled = isBusy() || !valid;
      message.textContent = missing.length
        ? tr(`所需依据当前不可用：${missing.join("、")}。请负责人核查来源。`, `Required evidence is unavailable: ${missing.join(", ")}. Ask the reviewer to check the source.`)
        : !availableEvidence.length ? tr("当前没有与此任务匹配的已准入依据。", "No admitted evidence is available for this task.") : "";
    }
    function updateTask(retainedRefs = records(draft.evidence_refs)) {
      const selectedTask = tasks.find(item => item.task_id === task.value);
      choices(executor, records(selectedTask?.allowed_executors).map(item => ({value: item.executor_id, label: title(item)})), draft.executor_id);
      availableEvidence = records(recovery.evidence_options).filter(item => item.domain_id === selectedTask?.domain_id
        && records(selectedTask?.evidence_refs).includes(item.ref)
        && (!resuming || records(recovery.required_evidence_refs).includes(item.ref)));
      evidenceList.replaceChildren(); evidenceControls = [];
      for (const evidence of availableEvidence) {
        const label = node("label"), input = node("input"); input.type = "checkbox"; input.value = evidence.ref;
        input.checked = retainedRefs.includes(evidence.ref);
        const required = resuming && records(recovery.required_evidence_refs).includes(evidence.ref);
        label.append(input, node("span", `${title(evidence)}${required ? tr("（必需）", " (required)") : ""}`), node("small", `${evidence.ref}\n${evidence.digest}`));
        evidenceList.append(label); evidenceControls.push({evidence, control: input}); input.addEventListener("change", remember);
      }
      validate();
    }
    task.addEventListener("change", () => { updateTask([]); remember(); });
    reason.addEventListener("input", remember); executor.addEventListener("change", remember);
    form.addEventListener("submit", event => {
      event.preventDefault(); validate();
      if (submit.disabled) return;
      const evidence = evidenceControls.filter(item => item.control.checked).map(item => ({ref: item.evidence.ref, digest: item.evidence.digest}));
      return onCommand?.(action, returning
        ? {expected_context_digest: recovery.context_digest, task_id: task.value, reason: reason.value.trim(), required_evidence_refs: evidence.map(item => item.ref)}
        : {recovery_digest: recovery.recovery_digest, evidence, executor_id: executor.value});
    });
    updateTask(); section.append(form);
  }
  window.OrgRebaseChangeRecovery = Object.freeze({render});
})();
