(() => {
  "use strict";
  const modes = { bpi: "INDEPENDENT_RECOMPUTATION", formation: "NEW_DETERMINISTIC_TASKFLOW",
    skill: "EXACT_PREDECESSOR_REPLAY", owb: "REFERENCE_SUT_REEXECUTION" };
  const copy = {
    zh: { title: "可靠性验证", boundary: "核对当前代码下的影响范围、组队与恢复能力。每项结果保留独立来源和验证范围。", checked: "本批核对完成", missing: "本安装尚无可核对的重验报告。历史档案与当前任务状态分别保留。",
      ready: "项核对可用", origin: "原始档案", execution: "本次新任务链运行标识", replayIdentity: "重放保留的原运行标识", details: "查看来源与核验摘要", report: "本次核验制品", noRun: "离线复算，未创建业务运行", invalid: "报告不完整或绑定无效，未展示结果", stale: "实现或输入已改变，需要重新核对", unavailable: "未取得报告", pass: "本次核对通过",
      names: { bpi: "采购日志范围复算", formation: "任务组队", skill: "Skill版本恢复", owb: "机制对照" },
      benefits: { bpi: "按固定凭证与行项目规则复算范围收缩。", formation: "检查任务是否交给所需领域。", skill: "检查精确旧版本能否恢复并调用。", owb: "比较控制机制缺失时的失败情况。" },
      sources: { bpi: "BPI 2019公开采购日志投影", formation: "受控领域任务", skill: "受控输入与保留的旧版本", owb: "合成案例与参考实现" },
      methods: { bpi: "按原输入独立重算", formation: "新建无模型任务链", skill: "原输入与精确旧版本重放", owb: "重新执行参考SUT" },
      scope: { bpi: "策略与真值使用同一范围规则；未运行产品影响引擎，不代表影响识别准确率或ROI", formation: "只产生候选，规范写入为0", skill: "受控恢复，不宣称生产灰度或进程重启", owb: "合成参考策略，不是实际单/多Agent模型比较" },
      metrics: { bpi: s => `${s.queries} 个查询 · ${s.strategies} 个策略`, formation: s => `${s.domains.length} 个领域 · ${s.task_bindings} 个任务 · ${s.agentteams_actions} 个原生动作`, skill: s => `${s.negative_probes} 个拒绝探针 · 恢复已核对`, owb: s => `${s.profiles} 个策略 × ${s.cases_per_profile} 个案例 · ${s.failed_strategy_profiles.length} 个策略未通过` } },
    en: { title: "Reliability checks", boundary: "Check impact scoping, team formation and recovery against the current code. Each result retains its own source and validation scope.", checked: "Batch check completed", missing: "This installation has no usable revalidation report. Historical archives and the current task remain separate.", ready: "checks usable", origin: "Original archive", execution: "New task-flow run identity", replayIdentity: "Original run identity retained by replay", details: "Inspect source and verification digests", report: "Recheck artifact", noRun: "Offline recomputation; no business run created", invalid: "Report incomplete or invalidly bound; results withheld", stale: "Implementation or inputs changed; recheck required", unavailable: "No report available", pass: "Recheck passed",
      names: { bpi: "Procurement scope replay", formation: "Task formation", skill: "Skill version recovery", owb: "Mechanism comparison" },
      benefits: { bpi: "Recompute scope reduction under fixed document and item rules.", formation: "Check that tasks reach the required domains.", skill: "Check that an exact predecessor can be restored and invoked.", owb: "Compare failures when control mechanisms are absent." },
      sources: { bpi: "Projection of public BPI 2019 procurement logs", formation: "Controlled domain tasks", skill: "Controlled inputs and a retained predecessor", owb: "Synthetic cases and reference implementations" },
      methods: { bpi: "Independent recomputation of original inputs", formation: "New model-free task flow", skill: "Replay original inputs and exact predecessor", owb: "Reexecute the reference SUT" },
      scope: { bpi: "Strategy and labels use the same scope rule; the product impact engine is not run. No impact-accuracy or ROI claim.", formation: "Candidates only; zero canonical writes", skill: "Controlled recovery, not production rollout or process restart", owb: "Synthetic reference strategies, not real single/multi-Agent model comparison" },
      metrics: { bpi: s => `${s.queries} queries · ${s.strategies} strategies`, formation: s => `${s.domains.length} domains · ${s.task_bindings} tasks · ${s.agentteams_actions} native actions`, skill: s => `${s.negative_probes} rejection probes · recovery checked`, owb: s => `${s.profiles} profiles × ${s.cases_per_profile} cases · ${s.failed_strategy_profiles.length} profiles failed` } },
  };
  const directory = {
    zh: [
      ["本次任务记录", "核对交付版本、批准人与生效依据。", "来源：当前工作区已保存的运行回执"],
      ["公开数据案例", "采购规则变化会影响哪些订单？", "BPI 2019 匿名采购日志 · 机制案例"],
      ["可靠性验证", "检查组队、恢复与权限隔离的验证结果。", "独立验证运行 · 保留失败与适用范围"],
    ],
    en: [
      ["Current task records", "Check the delivered version, approver and application evidence.", "Source: persisted receipts from this workspace"],
      ["Public-data case", "Which orders are affected by a procurement-rule change?", "Anonymized BPI 2019 logs · mechanism case"],
      ["Reliability checks", "Inspect team formation, recovery and authority isolation.", "Independent validation runs · failures and scope retained"],
    ],
  };
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const digest = value => typeof value === "string" && /^(?:sha256:)?[a-f0-9]{64}$/.test(value);
  const relativePath = value => typeof value === "string" && /^evidence\/[A-Za-z0-9_@./-]+$/.test(value)
    && !value.split("/").includes("..") && value.length < 512;
  function usable(item) {
    if (!item || item.status !== "PASS" || modes[item.id] !== item.mode || !relativePath(item.original_archive)
      || !Array.isArray(item.reason_codes) || item.reason_codes.length
      || !Array.isArray(item.artifacts) || !item.artifacts.length
      || !item.artifacts.every(a => relativePath(a.path) && digest(a.sha256))) return false;
    const s = item.summary || {};
    if (item.id === "bpi") return count(s.queries) && count(s.strategies);
    if (item.id === "formation") return Array.isArray(s.domains) && s.domains.length > 0
      && s.domains.every(d => typeof d === "string") && new Set(s.domains).size === s.domains.length
      && count(s.task_bindings) && count(s.agentteams_actions) && s.canonical_target_writes === 0;
    if (item.id === "skill") return count(s.negative_probes) && typeof s.restoration_status === "string"
      && s.restoration_status === "EXECUTED_AND_INVOKED"
      && s.canonical_target_writes === 0 && s.process_restart_proven === false;
    return count(s.profiles) && count(s.cases_per_profile) && Array.isArray(s.failed_strategy_profiles)
      && s.failed_strategy_profiles.every(p => typeof p === "string" && /^[a-z0-9-]+$/.test(p))
      && new Set(s.failed_strategy_profiles).size === s.failed_strategy_profiles.length
      && s.failed_strategy_profiles.length <= s.profiles && Number.isFinite(s.reference_score)
      && s.reference_score >= 0 && s.reference_score <= 100;
  }
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function renderDirectory(language) {
    const lang = language === "en" ? "en" : "zh";
    const navigation = document.getElementById("validation-directory");
    if (navigation) {
      navigation.setAttribute("aria-label", lang === "en" ? "Run records and validation" : "运行记录与验证");
      [...navigation.children].forEach((entry, index) => {
        const labels = directory[lang][index];
        if (labels) [...entry.children].forEach((element, part) => { if (labels[part]) element.textContent = labels[part]; });
        entry.onclick = event => {
          event.preventDefault();
          const target = document.getElementById(["current-run-archive", "public-real-process-validation", "archive-revalidation"][index]);
          if (!target) return;
          if (target.tagName === "DETAILS") target.open = true;
          target.scrollIntoView({behavior: "smooth", block: "start"});
        };
      });
    }
  }
  function render(view, language) {
    const root = document.getElementById("archive-revalidation");
    if (!root) return;
    const lang = language === "en" ? "en" : "zh", c = copy[lang];
    renderDirectory(language);
    root.replaceChildren();
    const header = node("header");header.append(node("h3", c.title), node("p", c.boundary));root.append(header);
    const items = view?.items;
    const valid = view?.schema_version === "orgrebase.archive-revalidation-view.v1"
      && ["PASS", "PARTIAL"].includes(view.status) && view.current_business_run === false && view.live_model_calls === 0
      && digest(view.manifest_digest) && typeof view.checked_at === "string" && Number.isFinite(Date.parse(view.checked_at))
      && Array.isArray(items) && items.length === 4 && new Set(items.map(i => i?.id)).size === 4
      && items.every(i => i && Object.hasOwn(modes, i.id) && modes[i.id] === i.mode
        && ["PASS", "STALE", "INVALID", "UNAVAILABLE"].includes(i.status));
    if (!valid) { root.append(node("p", c.missing, "archive-recheck-unavailable")); return; }
    const passed = items.filter(usable).length;
    const summary = node("p", `${passed}/${items.length} ${c.ready} · ${c.checked}: ${new Date(view.checked_at).toLocaleString(language === "en" ? "en-US" : "zh-CN")}`);
    root.append(summary);
    const grid = node("div", null, "archive-recheck-grid");root.append(grid);
    for (const item of items) {
      const pass = usable(item), card = node("article", null, "archive-recheck-card");
      card.dataset.status = pass ? "PASS" : item.status === "PASS" ? "INVALID" : item.status;
      card.append(node("h4", c.names[item.id]), node("p", c.benefits[item.id]), node("small", c.sources[item.id], "archive-recheck-source"));
      card.append(node("strong", pass ? c.pass : item.status === "STALE" ? c.stale : item.status === "UNAVAILABLE" ? c.unavailable : c.invalid));
      if (pass) card.append(node("p", c.metrics[item.id](item.summary), "archive-recheck-metrics"));
      card.append(node("small", c.scope[item.id]));
      const details = node("details"), list = node("dl");details.append(node("summary", c.details), node("p", c.methods[item.id], "archive-recheck-method"), list);
      for (const [label, value] of [[c.origin, relativePath(item.original_archive) ? item.original_archive : "—"],
        [item.id === "skill" ? c.replayIdentity : c.execution, pass && typeof item.execution_run_id === "string" && /^run:[A-Za-z0-9:_@.-]+$/.test(item.execution_run_id) ? item.execution_run_id : c.noRun]]) {
        list.append(node("dt", label), node("dd", value));
      }
      if (pass) for (const artifact of item.artifacts) list.append(node("dt", c.report), node("dd", `${artifact.path} · ${artifact.sha256}`));
      card.append(details);grid.append(card);
    }
  }
  renderDirectory(document.documentElement.lang);
  window.addEventListener("orgrebase:languagechange", event => {
    renderDirectory(event.detail?.language);
  });
  window.OrgRebaseArchiveRevalidation = Object.freeze({ render });
})();
