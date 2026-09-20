(() => {
  "use strict";
  const client = window.OrgRebaseClient;
  const mount = document.getElementById("skill-validation-mount");
  if (!client || !mount) return;
  const endpoint = "/api/workspace/skills/enterprise-quote-compose/validation";
  const tr = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const element = (tag, text, className = "") => {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  };
  const root = element("section", "", "capability-section");
  const title = element("h3", "");
  const boundary = element("p", "");
  const versions = element("p", "");
  const status = element("p", "");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const stages = element("div", "", "binding-grid skill-grid");
  const compatibility = element("div", "", "binding-grid skill-grid");
  compatibility.id = "skill-compatibility-catalog";
  const actions = element("div", "", "change-workbench-actions");
  const execute = element("button", "", "button button-primary");
  execute.id = "skill-validation-run";
  execute.type = "button";
  const refresh = element("button", "", "button button-secondary");
  refresh.id = "skill-validation-refresh";
  refresh.type = "button";
  const technical = element("details", "", "skill-technical-details");
  const technicalTitle = element("summary", "");
  const evidence = element("pre", "");
  evidence.style.whiteSpace = "pre-wrap";
  evidence.style.overflowWrap = "anywhere";
  technical.append(technicalTitle, evidence);
  actions.append(execute, refresh);
  root.append(title, boundary, versions, status, stages, compatibility, actions, technical);
  mount.append(root);
  let view = null;
  let busy = false;
  let failure = null;
  let generation = 0;
  const context = () => {
    const session = client.session();
    return JSON.stringify([client.workspace(), session?.mode, session?.authenticated,
      session?.principal?.tenant_id, session?.principal?.actor_id, session?.principal?.subject,
      session?.principal?.roles, session?.csrf_token]);
  };

  function render() {
    title.textContent = tr("核心 Skill：版本兼容与恢复验证", "Core Skill: version compatibility and recovery");
    boundary.textContent = tr(
      "用受控样本检查版本兼容、试调用和旧版本恢复。结果单独保存，当前 Skill 和报价保持不变。",
      "Check version compatibility, trial execution and predecessor recovery with controlled samples. Results are saved separately; the active Skill and quote stay unchanged.");
    versions.textContent = view ? `${view.name} · ${view.predecessor_version} → ${view.current_version} → ${view.predecessor_version}`
      + (view.program_unchanged ? tr("。两个版本使用相同执行程序，本次检查兼容与恢复。", ". Both versions use the same executable program; this check covers compatibility and recovery.") : "") : "";
    const state = view?.state;
    const labels = {
      NOT_RUN: tr("尚未验证。Skill 负责人可检查三个核心 Skill，最多执行 38 次受控调用和 1 次本地依赖读取。", "Not checked yet. The Skill Steward can check three core Skills with up to 38 controlled calls and one local dependency read."),
      IN_PROGRESS: tr("验证正在运行；刷新查看结果，系统不会重复执行。", "Validation is running. Refresh for its result; execution is not repeated."),
      RESULT_UNKNOWN: tr("运行结果尚未确认。保留原记录，不自动重试。", "The result is unconfirmed. The original attempt is retained without automatic retry."),
      FAILED: tr("本次验证失败，未改变业务能力。请核查错误回执。", "Validation failed. Business capability is unchanged. Inspect the failure receipt."),
      PASS: tr("检查通过，旧版本已恢复并核对。结果已保存，可随时查阅。", "Checks passed. Predecessor recovery was executed and verified; the saved result remains available."),
    };
    status.textContent = failure ? tr("请求未确认，请刷新核对；不会自动重发。", "Request not confirmed. Refresh to reconcile; it will not be resent automatically.") + ` (${failure})`
      : busy ? tr("正在读取或执行验证…", "Reading or executing validation…") : labels[state] || tr("正在读取验证范围…", "Loading validation scope…");
    root.dataset.state = failure ? "unavailable" : state || "loading";
    root.setAttribute("aria-busy", String(busy));
    const session = client.session();
    const allowed = session && (session.mode === "local" && !session.authentication_required
      || session.authenticated && session.principal?.actor_id === view?.required_actor);
    execute.textContent = tr("Skill 负责人：运行受控验证", "Skill Steward: run controlled validation");
    execute.disabled = busy || !allowed || !view?.can_execute || state !== "NOT_RUN";
    refresh.textContent = tr("刷新验证结果", "Refresh validation result");
    refresh.disabled = busy;
    const proof = state === "PASS" ? view?.result?.evidence : null;
    stages.replaceChildren();
    compatibility.replaceChildren();
    if (proof) {
      const cards = [
        [tr("保留版本", "Retained version"), view.predecessor_version, proof.baseline.output_digest],
        [tr("资格检查", "Qualification"), `${proof.evaluation.case_results.filter(item => item.passed).length} / ${proof.evaluation.case_results.length}`, proof.evaluation.digest],
        [tr("受控试调用", "Controlled trial"), view.current_version, proof.trial.receipt.digest],
        [tr("执行恢复", "Recovery executed"), view.predecessor_version, proof.rollback.receipt.digest],
      ];
      for (const [label, value, digest] of cards) {
        const card = element("article", "", "binding-card");
        const receipt = element("small", `${digest.slice(0, 19)}…`, "machine-token");
        receipt.title = digest;
        receipt.style.overflowWrap = "anywhere";
        card.append(element("span", label), element("strong", value), receipt);
        stages.append(card);
      }
    }
    for (const pair of view?.compatibility_catalog || []) {
      const checked = proof?.compatibility?.find(item => item.name === pair.name);
      const card = element("article", "", "binding-card");
      card.append(element("strong", pair.name),
        element("span", `${pair.predecessor_version} → ${pair.current_version}`),
        element("span", checked
          ? tr("6 项兼容检查通过 · 原始前驱已调用", "6 compatibility checks passed · retained predecessor invoked")
          : tr("前驱文件已保留 · 兼容检查待运行", "Predecessor files retained · compatibility checks pending")),
        element("small", tr("隔离检查；业务版本不变。", "Isolated checks; the business version stays unchanged.")));
      compatibility.append(card);
    }
    technicalTitle.textContent = tr("版本来源、调用和恢复回执", "Version sources, invocation and recovery receipts");
    technical.hidden = !view;
    evidence.textContent = view ? JSON.stringify({ binding: view.binding, source: view.source, limits: view.limits, result: view.result }, null, 2) : "";
  }

  function validView(value) {
    const digest = (item) => typeof item === "string" && /^sha256:[0-9a-f]{64}$/.test(item);
    const binding = value?.binding;
    const result = value?.result;
    const proof = result?.evidence;
    const allowedStates = ["NOT_RUN", "IN_PROGRESS", "RESULT_UNKNOWN", "FAILED", "PASS"];
    const catalog = value?.compatibility_catalog;
    const names = ["enterprise-launch-readiness", "structured-domain-handoff"];
    const qualificationCases = [
      ["replay", "REPLAY"], ["held_out", "HELD_OUT"], ["negative_transfer", "NEGATIVE_TRANSFER"],
      ["permission", "PERMISSION"], ["injection", "INJECTION"], ["malformed", "MALFORMED"],
      ["resource_or_deadline", "RESOURCE_OR_DEADLINE"], ["canary", "CANARY"],
      ["domain_substitution", "MALFORMED"],
    ];
    const qualificationBound = binding?.qualification_suite_revision === "orgrebase.quote-skill-qualification.v2"
      && binding?.qualification_suite_digest === "sha256:7b272a0f3da13f2c8d7db1bd87afd797cbca28e4e3e5c569120a8e7f54e19ea7";
    const qualified = value?.state !== "PASS" || qualificationBound
      && proof?.evaluation?.premise_lock?.qualification_suite_revision === binding.qualification_suite_revision
      && proof?.evaluation?.premise_lock?.qualification_suite_digest === binding.qualification_suite_digest
      && typeof proof?.run_id === "string" && Array.isArray(proof?.evaluation?.case_results)
      && proof.evaluation.case_results.length === qualificationCases.length
      && qualificationCases.every(([name, partition]) => proof.evaluation.case_results.filter(
        item => item?.case_ref === `${proof.run_id}:quote-compose:${name}` && item.partition === partition
          && item.passed === true).length === 1);
    const validCatalog = Array.isArray(catalog) && catalog.length === names.length
      && names.every(name => catalog.filter(pair => pair?.name === name).length === 1)
      && catalog.every(pair => digest(pair.package_digest) && digest(pair.predecessor_digest)
        && digest(pair.provenance_digest) && typeof pair.current_version === "string"
        && typeof pair.predecessor_version === "string");
    const compatible = value?.state !== "PASS" || validCatalog && Array.isArray(proof?.compatibility)
      && proof.compatibility.length === catalog.length && catalog.every(pair => {
        const results = proof.compatibility.filter(item => item?.name === pair.name);
        const checked = results[0];
        return results.length === 1 && checked.scope === "ISOLATED_COMPATIBILITY_AND_PREDECESSOR_INVOCATION"
          && checked.package_digest === pair.package_digest && checked.predecessor_digest === pair.predecessor_digest
          && checked.provenance_digest === pair.provenance_digest && digest(checked.digest)
          && checked.synthetic_inputs === true && checked.predecessor_replay_equal === true
          && checked.business_skill_pointer_writes === 0 && checked.canonical_target_writes === 0
          && Array.isArray(checked.cases) && checked.cases.length === 6
          && checked.cases.every(item => item?.passed === true)
          && digest(checked.predecessor_replay?.output_digest);
      });
    const complete = value?.state !== "PASS" || result?.status === "PASS"
      && proof?.scope === "ISOLATED_CONTROLLED_SKILL_VALIDATION"
      && proof.synthetic_inputs === true && proof.restored_baseline_equal === true
      && proof.business_skill_pointer_writes === 0 && proof.canonical_target_writes === 0
      && digest(result.digest) && digest(proof.baseline?.output_digest)
      && proof.evaluation?.verdict === "CANARY" && digest(proof.evaluation.digest)
      && qualified
      && digest(proof.trial?.receipt?.digest) && proof.trial.receipt.package_digest === binding?.package_digest
      && digest(proof.rollback?.receipt?.digest) && proof.rollback.receipt.effective_package_digest === binding?.predecessor_digest
      && proof.rollback.receipt.restoration_status === "EXECUTED_AND_INVOKED";
    return value?.schema_version === "orgrebase.skill-validation.v1" && complete && compatible && validCatalog
      && qualificationBound
      && allowedStates.includes(value.state) && typeof value.name === "string"
      && typeof value.current_version === "string" && typeof value.predecessor_version === "string"
      && digest(binding?.package_digest) && digest(binding?.predecessor_digest)
      && digest(binding?.compatibility_catalog_digest)
      && value.binding?.workspace_id === client.workspace()
      && (!client.session()?.principal || value.binding.tenant_id === client.session().principal.tenant_id)
      && (!result || result.binding && Object.keys(binding).length === Object.keys(result.binding).length
        && Object.keys(binding).every(key => result.binding[key] === binding[key]))
      && value.synthetic_inputs === true && value.production_canary === false
      && value.business_skill_pointer_writes === 0 && value.canonical_target_writes === 0;
  }

  async function request(run) {
    if (busy || run && (!view?.can_execute || execute.disabled)) return;
    const token = ++generation;
    busy = true;
    failure = null;
    render();
    try {
      if (!client.session()) await client.refreshSession();
      if (!client.workspace()) await client.refreshWorkspaces();
      if (token !== generation) return;
      const expectedContext = context();
      const options = run ? { method: "POST", headers: { "X-OrgRebase-Actor": view.required_actor }, body: {
        actor_id: view.required_actor, expected_package_digest: view.binding.package_digest,
        expected_predecessor_digest: view.binding.predecessor_digest,
        expected_catalog_digest: view.binding.compatibility_catalog_digest,
      } } : {};
      const received = await client.json(endpoint, options);
      if (token !== generation) return;
      if (context() !== expectedContext) { view = null; failure = "WORKSPACE_REQUEST_CONTEXT_CHANGED"; return; }
      if (!validView(received)) throw new Error("SKILL_VALIDATION_SCOPE_INVALID");
      view = received;
    } catch (error) {
      if (token !== generation) return;
      failure = error.code || "SKILL_VALIDATION_UNAVAILABLE";
      if (run && view) view = { ...view, can_execute: false };
      else view = null;
    } finally {
      if (token === generation) { busy = false; render(); }
    }
  }
  execute.addEventListener("click", () => request(true));
  refresh.addEventListener("click", () => request(false));
  window.addEventListener("orgrebase:languagechange", render);
  window.addEventListener("orgrebase:sessionchange", () => {
    ++generation; busy = false; view = null; failure = null; render();
    const session = client.session();
    if (session && (!session.authentication_required || session.authenticated)) request(false);
  });
  window.addEventListener("orgrebase:workspacechange", () => { ++generation; busy = false; view = null; request(false); });
  window.addEventListener("orgrebase:sessionended", () => { ++generation; busy = false; view = null; failure = "AUTH_SESSION_REQUIRED"; render(); });
  render();
  request(false);
})();
