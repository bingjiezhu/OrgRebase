(() => {
  "use strict";

  const client = window.OrgRebaseClient;
  const mount = document.getElementById("experience-library-mount");
  if (!client || !mount) return;

  const CASES = "/api/workspace/experience-cases?limit=50";
  const LESSONS = "/api/workspace/experience-lessons?limit=50";
  const CANDIDATES = "/api/workspace/experience-lessons/candidates?limit=20";
  const HEAD = "/api/workspace/skills/finance-change-explanation/head";
  const PROFILE = "workspace-change-explanation-v1";
  const eventPattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/;
  const tr = (zh, en) => document.documentElement.lang === "en" ? en : zh;
  const make = (tag, className = "", value = "") => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = value;
    return node;
  };
  const title = document.getElementById("experience-library-title");
  const boundary = document.getElementById("experience-library-boundary");
  const toolbar = make("div", "experience-library-toolbar");
  const status = make("p", "experience-library-status");
  status.setAttribute("role", "status");
  const refresh = make("button", "button button-secondary");
  refresh.type = "button";
  refresh.id = "experience-library-refresh";
  const stages = make("div", "experience-library-stages");
  const caseTitle = make("h3", "experience-library-case-title");
  const caseBoundary = make("p", "experience-library-case-boundary");
  const cases = make("ul", "experience-library-cases");
  const caseDetailPanel = make("div", "experience-library-detail");
  const lessonTitle = make("h3", "experience-library-case-title");
  const lessonBoundary = make("p", "experience-library-case-boundary");
  const lessons = make("ul", "experience-library-cases");
  const lessonDetailPanel = make("div", "experience-library-detail");
  const candidateTitle = make("h3", "experience-library-case-title");
  const candidateBoundary = make("p", "experience-library-case-boundary");
  const candidates = make("ul", "experience-library-cases");
  const evaluation = make("p", "experience-library-evaluation");
  toolbar.append(status, refresh);
  mount.append(toolbar, stages, caseTitle, caseBoundary, cases, caseDetailPanel,
    lessonTitle, lessonBoundary, lessons, lessonDetailPanel,
    candidateTitle, candidateBoundary, candidates, evaluation);

  let generation = 0;
  let detailGeneration = 0;
  let eventGeneration = 0;
  let loading = false;
  let activeEventId = null;
  let eventId = null;
  let casePage = null;
  let lessonPage = null;
  let candidatePage = null;
  let selectedCaseRef = null;
  let selectedLessonId = null;
  let caseDetailView = null;
  let lessonDetailView = null;
  let caseDetailError = null;
  let lessonDetailError = null;
  let candidateError = null;
  let head = null;
  let evaluationView = null;
  let caseError = null;
  let lessonError = null;
  let headError = null;
  let evaluationError = null;

  const scope = () => {
    const session = client.session();
    return JSON.stringify([
      client.workspace(), session?.mode, session?.identity_source,
      session?.authenticated, session?.principal?.issuer,
      session?.principal?.subject, session?.principal?.tenant_id,
      session?.principal?.actor_id, session?.principal?.roles,
      session?.csrf_token,
    ]);
  };
  const permittedSession = () => {
    const session = client.session();
    return Boolean(session && (!session.authentication_required || session.authenticated));
  };
  const reviewerSession = () => permittedSession()
    && client.session()?.principal?.roles?.includes("governor");
  const digest = value => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
  const code = error => typeof error?.code === "string" ? error.code : "REQUEST_FAILED";

  function acceptCases(value) {
    if (value?.schema_version !== "orgrebase.experience-case-page.v1"
      || !Array.isArray(value.items) || value.items.length > 50
      || value.next_cursor != null && typeof value.next_cursor !== "string"
      || value.items.some(item => !item || typeof item.case_ref !== "string"
        || !digest(item.case_digest) || typeof item.profile_id !== "string"
        || !["OBSERVED", "QUARANTINED"].includes(item.observation_status)
        || typeof item.assessment !== "string" || !/^[A-Z_]+$/.test(item.assessment)
        || item.assessment_profile_id !== PROFILE
        || !["COMPLETE", "PARTIAL_COVERAGE", "UNKNOWN"].includes(item.assessment_coverage)
        || item.assessment_count_observed != null
          && (!Number.isSafeInteger(item.assessment_count_observed)
            || item.assessment_count_observed < 0)
        || !["APPLIED", "REJECTED", "PENDING", "UNKNOWN"].includes(item.execution_outcome))) {
      throw new Error("EXPERIENCE_CASE_PAGE_INVALID");
    }
    return value;
  }

  function acceptHead(value) {
    if (!value || value.profile_id !== PROFILE || typeof value.head_ref !== "string"
      || !digest(value.head_digest) || !digest(value.package_digest)
      || !Number.isSafeInteger(value.generation) || value.generation < 0
      || !["UNQUALIFIED", "QUALIFIED"].includes(value.qualification_status)
      || typeof value.adoption_enabled !== "boolean") {
      throw new Error("FINANCE_HEAD_VIEW_INVALID");
    }
    return value;
  }

  function acceptLessons(value) {
    if (value?.schema_version !== "orgrebase.experience-lesson-page.v1"
      || !Array.isArray(value.items) || value.items.length > 50
      || value.next_cursor != null && typeof value.next_cursor !== "string"
      || value.items.some(item => !item || typeof item.head_ref !== "string"
        || !digest(item.head_digest) || !["QUALIFIED_FOR_RECALL", "HOLD"].includes(item.status)
        || item.content_bytes_disclosed !== 0
        || item.problem_code != null && typeof item.problem_code !== "string"
        || item.support_cluster_count != null && (!Number.isSafeInteger(item.support_cluster_count)
          || item.support_cluster_count < 0))) {
      throw new Error("EXPERIENCE_LESSON_PAGE_INVALID");
    }
    return value;
  }

  function acceptCaseDetail(value, expectedRef) {
    if (!value || value.schema_version !== "orgrebase.experience-case-detail.v1"
      || value.case_ref !== expectedRef || !digest(value.case_digest)
      || !["CURRENT", "HOLD"].includes(value.source_state)
      || value.source_reason_code != null && typeof value.source_reason_code !== "string"
      || typeof value.assessment_status !== "string"
      || value.assessment_profile_id !== PROFILE
      || value.private_content_disclosed !== false
      || value.actual_use_status !== "NOT_CHECKED") {
      throw new Error("EXPERIENCE_CASE_DETAIL_INVALID");
    }
    return value;
  }

  function acceptLessonDetail(value, expectedId) {
    if (!value || value.schema_version !== "orgrebase.experience-lesson-detail.v1"
      || value.lesson_id !== expectedId || !digest(value.head_digest)
      || !["QUALIFIED_FOR_RECALL", "HOLD"].includes(value.status)
      || !Array.isArray(value.related_case_refs)
      || value.related_case_refs.some(ref => typeof ref !== "string")
      || !["COMPLETE", "PARTIAL_COVERAGE"].includes(value.related_case_coverage)
      || !["RECALLED", "NOT_OBSERVED", "PARTIAL_COVERAGE"].includes(value.retrieval_status)
      || value.content_bytes_disclosed !== 0
      || value.actual_use_status !== "NOT_CHECKED") {
      throw new Error("EXPERIENCE_LESSON_DETAIL_INVALID");
    }
    return value;
  }

  function acceptCandidates(value) {
    if (value?.schema_version !== "orgrebase.experience-candidate-page.v1"
      || !Array.isArray(value.items) || value.items.length > 20
      || value.next_cursor != null && typeof value.next_cursor !== "string"
      || value.items.some(item => !item || typeof item.candidate_ref !== "string"
        || typeof item.lesson_id !== "string" || item.profile_id !== PROFILE
        || !["CURRENT_QUALIFIED", "HOLD"].includes(item.evidence_status)
        || !["READY_FOR_REVIEW", "HOLD"].includes(item.review_status)
        || !["CURRENT", "NOT_CURRENT"].includes(item.publication_status)
        || item.content_bytes_disclosed !== 0)) {
      throw new Error("EXPERIENCE_CANDIDATE_PAGE_INVALID");
    }
    return value;
  }

  function acceptEvaluation(value, expectedEventId) {
    if (!value || value.event_id !== expectedEventId
      || !["NOT_STARTED", "RESULT_UNKNOWN", "PROTOCOL_VALID", "HOLD", "NOT_SENT", "FAILED", "REJECTED"].includes(value.status)
      || value.target_writes !== 0
      || value.status === "PROTOCOL_VALID"
        && !["CURRENT_INPUTS", "HOLD"].includes(value.current_qualification)
      || value.quality_status != null && typeof value.quality_status !== "string") {
      throw new Error("FINANCE_EVALUATION_VIEW_INVALID");
    }
    return value;
  }

  function stage(label, result, detail, phase) {
    const card = make("article", "experience-library-stage");
    card.dataset.phase = phase;
    card.append(make("span", "", label), make("strong", "", result), make("small", "", detail));
    return card;
  }

  function render() {
    title.textContent = tr("经验与 Finance 指导", "Experience and Finance guidance");
    boundary.textContent = tr("观察、独立评估、治理发布与实际使用分别核对", "Observation, independent evaluation, governed release and actual use are checked separately");
    refresh.textContent = tr("刷新记录", "Refresh records");
    refresh.disabled = loading || !permittedSession();
    mount.setAttribute("aria-busy", String(loading));
    const unavailable = !permittedSession();
    status.textContent = unavailable
      ? tr("请先确认当前会话；旧身份的记录已清空。", "Confirm the current session. Records from the previous identity have been cleared.")
      : loading ? tr("正在读取当前工作区记录…", "Reading records for this workspace…")
        : caseError || lessonError || headError || evaluationError
          ? tr("部分记录暂不可读；各阶段保留自己的未确认状态。", "Some records are unavailable; each stage keeps its own unconfirmed state.")
          : tr("仅展示当前身份可读取的低敏记录。", "Showing only low-sensitivity records readable by this identity.");

    const observed = casePage?.items.length || 0;
    const caseResult = caseError
      ? tr("读取未确认", "Read unconfirmed")
      : !casePage ? tr("NOT_RUN · 未读取", "NOT_RUN · not read")
        : observed ? tr(`已观察 ${observed} 条`, `${observed} observed`)
          : tr("未见案例", "No cases observed");
    const caseDetail = casePage?.next_cursor
      ? tr("仅当前页；后续记录未读取。", "Current page only; later records have not been read.")
      : tr("业务结果不等于学习成功。", "A business outcome is not a learning result.");

    let evaluationResult = tr("NOT_RUN · 未评估", "NOT_RUN · not evaluated");
    let evaluationDetail = tr("没有独立质量证书。", "No independent quality certificate.");
    if (evaluationError) {
      evaluationResult = tr("读取未确认", "Read unconfirmed");
      evaluationDetail = tr("当前身份或记录不可用。", "Identity or record unavailable.");
    } else if (evaluationView?.status === "RESULT_UNKNOWN") {
      evaluationResult = tr("结果未知", "Result unknown");
      evaluationDetail = tr("保留原操作，不推断质量。", "Original operation retained; quality remains unknown.");
    } else if (evaluationView?.status === "PROTOCOL_VALID"
      && evaluationView.current_qualification === "HOLD") {
      evaluationResult = tr("历史协议通过 · 当前失效", "Historical protocol pass · current inputs stale");
      evaluationDetail = tr("旧结果保留；当前来源、版本或私密证据不可再用于资格。", "Historical result retained; current source, head or private evidence cannot qualify it.");
    } else if (evaluationView?.status === "PROTOCOL_VALID") {
      evaluationResult = tr("协议检查完成", "Protocol check complete");
      evaluationDetail = tr("质量未评；不代表解释改善。", "Quality not evaluated; no improvement claim.");
    } else if (evaluationView && evaluationView.status !== "NOT_STARTED") {
      evaluationResult = tr("隔离运行未通过", "Isolated run did not pass");
      evaluationDetail = tr("请查看受权的原评测回执。", "Inspect the authorized evaluation receipt.");
    }

    const published = head?.generation > 0 && head.qualification_status === "QUALIFIED";
    const releaseResult = headError === "FINANCE_SKILL_HEAD_NOT_BOOTSTRAPPED"
      ? tr("NOT_RUN · 未建立起点", "NOT_RUN · no baseline")
      : headError ? tr("读取未确认", "Read unconfirmed")
        : !head ? tr("NOT_RUN · 未读取", "NOT_RUN · not read")
          : published ? tr(`已发布 · 第 ${head.generation} 代`, `Released · generation ${head.generation}`)
            : tr(`静态起点 · 第 ${head.generation} 代`, `Static baseline · generation ${head.generation}`);
    const releaseDetail = published
      ? tr("当前 head 已通过治理资格；使用另查。", "Current head is qualified; use is checked separately.")
      : tr("起点存在不等于评估、发布或采用。", "A baseline does not establish evaluation, release or adoption.");
    const useResult = head?.adoption_enabled
      ? tr("允许选择 · 使用未观测", "Selection enabled · use unobserved")
      : tr("NOT_RUN · 使用未观测", "NOT_RUN · use unobserved");
    stages.replaceChildren(
      stage(tr("01 · 案例观察", "01 · Case observation"), caseResult, caseDetail, observed ? "observed" : "pending"),
      stage(tr("02 · 独立评估", "02 · Independent evaluation"), evaluationResult, evaluationDetail, "pending"),
      stage(tr("03 · 治理发布", "03 · Governed release"), releaseResult, releaseDetail, published ? "released" : "pending"),
      stage(tr("04 · 实际采用", "04 · Actual use"), useResult,
        tr("当前只读接口没有使用回执；不可把启用开关算作已采用。", "The current read surface has no use receipt; an enabled switch is not adoption."), "pending"),
    );

    caseTitle.textContent = tr("普通业务观察", "Ordinary business observations");
    caseBoundary.textContent = tr("只显示本页最多五条案例元数据；未审轨迹与经验正文不在这里展开。", "Up to five metadata rows from this page; raw trajectories and lesson text are not shown here.");
    cases.replaceChildren();
    for (const item of casePage?.items.slice(0, 5) || []) {
      const row = make("li", "experience-library-case");
      row.append(make("strong", "", item.profile_id), make("code", "", item.case_ref),
        make("small", "", `${item.observation_status} · ${item.execution_outcome} · Finance assessment: ${item.assessment}`
          + (item.assessment_coverage === "COMPLETE" ? "" : ` · ${item.assessment_coverage}`)
          + (item.source_state === "HOLD" ? ` · ${item.source_reason_code || "SOURCE_HOLD"}` : "")));
      const open = make("button", "button button-secondary", tr("查看案例详情", "View case detail"));
      open.type = "button";
      open.addEventListener("click", () => loadCaseDetail(item.case_ref));
      row.append(open);
      cases.append(row);
    }
    if (!casePage?.items.length) {
      cases.append(make("li", "experience-library-case-empty", caseError
        ? tr("案例读取失败或当前身份无权查看。", "Cases could not be read or this identity lacks access.")
        : tr("当前页没有可展示的观察记录。", "No observation records to display on this page.")));
    }
    if (casePage?.items.length > 5) cases.append(make("li", "experience-library-case-empty",
      tr(`本页另有 ${casePage.items.length - 5} 条；完整页可用受权 CLI/API 查看。`,
        `${casePage.items.length - 5} more on this page; use the authorized CLI/API for the full page.`)));
    caseDetailPanel.replaceChildren();
    if (caseDetailView && selectedCaseRef) {
      caseDetailPanel.append(
        make("strong", "", tr("案例详情", "Case detail")),
        make("code", "", selectedCaseRef),
        make("small", "", `${tr("来源", "Source")}: ${caseDetailView.source_state}`
          + (caseDetailView.source_reason_code ? ` · ${caseDetailView.source_reason_code}` : "")
          + ` · ${tr("独立评估", "Independent assessment")}: ${caseDetailView.assessment_status}`
          + ` · ${tr("实际使用", "Actual use")}: ${caseDetailView.actual_use_status}`),
      );
    } else if (selectedCaseRef) {
      caseDetailPanel.append(make("small", "", caseDetailError
        ? tr("详情不可读或当前资格未确认。", "Detail unavailable or current qualification unconfirmed.")
        : tr("正在读取案例详情…", "Reading case detail…")));
    }
    lessonTitle.textContent = tr("受审经验条目", "Reviewed lesson entries");
    lessonBoundary.textContent = tr(
      "私密候选不在此列表；可召回只表示当前读取资格，实际使用另需回执。",
      "Private candidates are not listed; recall eligibility is not an actual-use receipt.");
    lessons.replaceChildren();
    for (const item of lessonPage?.items.slice(0, 5) || []) {
      const row = make("li", "experience-library-case");
      const qualified = item.status === "QUALIFIED_FOR_RECALL";
      row.append(make("strong", "", qualified
        ? tr("可召回 · 使用未观测", "Eligible for recall · use unobserved")
        : tr("暂停使用", "On hold")),
      make("code", "", item.head_ref),
      make("small", "", qualified
        ? `${item.problem_code || "—"} · ${tr("独立来源簇", "independent clusters")} ${item.support_cluster_count ?? "—"}`
        : `${tr("原因", "Reason")}: ${item.reason_code || "UNKNOWN"}`));
      if (typeof item.lesson_id === "string" && item.lesson_id) {
        const open = make("button", "button button-secondary", tr("查看经验详情", "View lesson detail"));
        open.type = "button";
        open.addEventListener("click", () => loadLessonDetail(item.lesson_id));
        row.append(open);
      }
      lessons.append(row);
    }
    if (!lessonPage?.items.length) {
      lessons.append(make("li", "experience-library-case-empty", lessonError
        ? tr("经验条目暂不可读；其资格与使用均未确认。", "Lesson entries are unavailable; eligibility and use remain unconfirmed.")
        : tr("当前页没有受审经验条目。", "No reviewed lesson entries on this page.")));
    }
    if (lessonPage?.items.length > 5) lessons.append(make("li", "experience-library-case-empty",
      tr(`本页另有 ${lessonPage.items.length - 5} 条；完整页可用受权 CLI/API 查看。`,
        `${lessonPage.items.length - 5} more on this page; use the authorized CLI/API for the full page.`)));
    if (lessonPage?.next_cursor) lessons.append(make("li", "experience-library-case-empty",
      tr("仅显示当前页；后续条目未读取。", "Current page only; later entries have not been read.")));
    lessonDetailPanel.replaceChildren();
    if (lessonDetailView && selectedLessonId) {
      lessonDetailPanel.append(
        make("strong", "", tr("经验详情", "Lesson detail")),
        make("code", "", selectedLessonId),
        make("small", "", `${tr("维护", "Maintenance")}: ${lessonDetailView.maintenance_status || "UNKNOWN"}`
          + ` · ${tr("读取资格", "Read eligibility")}: ${lessonDetailView.status}`
          + (lessonDetailView.reason_code ? ` · ${lessonDetailView.reason_code}` : "")
          + ` · ${tr("历史召回", "Historical recall")}: ${lessonDetailView.retrieval_status}`
          + ` · ${tr("实际使用", "Actual use")}: ${lessonDetailView.actual_use_status}`),
        make("small", "", `${tr("关联案例", "Related cases")}: ${lessonDetailView.related_case_refs.join(", ") || "—"}`
          + (lessonDetailView.related_case_coverage === "COMPLETE" ? "" : " · PARTIAL_COVERAGE")),
      );
    } else if (selectedLessonId) {
      lessonDetailPanel.append(make("small", "", lessonDetailError
        ? tr("详情不可读或当前资格未确认。", "Detail unavailable or current qualification unconfirmed.")
        : tr("正在读取经验详情…", "Reading lesson detail…")));
    }
    candidateTitle.textContent = tr("未审与维护候选", "Private candidates and maintenance");
    candidateBoundary.textContent = tr(
      "仅指定治理者可读候选元数据；原文仍在私密记录中。",
      "Only the designated governor can read candidate metadata; text stays private.");
    candidates.replaceChildren();
    if (!reviewerSession()) {
      candidates.append(make("li", "experience-library-case-empty",
        tr("当前身份不读取治理候选。", "This identity does not read governed candidates.")));
    } else if (candidatePage?.items.length) {
      for (const item of candidatePage.items.slice(0, 5)) {
        const row = make("li", "experience-library-case");
        row.append(make("strong", "", item.lesson_id),
          make("code", "", item.candidate_ref),
          make("small", "", `${item.review_status} · ${item.evidence_status} · ${item.publication_status} · ${item.private_body_status}`
            + (item.evidence_reason_code ? ` · ${item.evidence_reason_code}` : "")));
        candidates.append(row);
      }
      if (candidatePage.items.length > 5 || candidatePage.next_cursor) {
        candidates.append(make("li", "experience-library-case-empty",
          tr("候选仅展示当前可见部分；更多记录请用治理者 CLI/API。",
            "Only part of the candidate page is shown; use the governor CLI/API for more.")));
      }
    } else {
      candidates.append(make("li", "experience-library-case-empty", candidateError
        ? tr("治理候选元数据不可读。", "Governed candidate metadata is unavailable.")
        : tr("当前页没有治理候选。", "No governed candidates on this page.")));
    }
    evaluation.textContent = !eventId
      ? tr("Finance 隔离评测：没有选定变化事项。", "Finance isolated evaluation: no change event selected.")
      : evaluationError
        ? tr("Finance 隔离评测：当前身份无法确认该事项的回执。", "Finance isolated evaluation: the receipt could not be confirmed for this identity.")
        : evaluationView?.status === "PROTOCOL_VALID"
          ? tr(`Finance 隔离评测 · ${eventId}：协议已验证，质量 ${evaluationView.quality_status || "NOT_EVALUATED"}；业务写入 0。`,
            `Finance isolated evaluation · ${eventId}: protocol verified, quality ${evaluationView.quality_status || "NOT_EVALUATED"}; business writes 0.`)
          : tr(`Finance 隔离评测 · ${eventId}：${evaluationView?.status || "NOT_RUN"}。`,
            `Finance isolated evaluation · ${eventId}: ${evaluationView?.status || "NOT_RUN"}.`);
  }

  async function loadCaseDetail(ref) {
    const token = ++detailGeneration;
    selectedCaseRef = ref;
    selectedLessonId = null;
    caseDetailView = null;
    lessonDetailView = null;
    caseDetailError = null;
    lessonDetailError = null;
    render();
    if (!permittedSession()) return;
    const expectedScope = scope();
    try {
      const value = await client.json(`/api/workspace/experience-cases/${encodeURIComponent(ref)}`);
      if (token !== detailGeneration || expectedScope !== scope() || selectedCaseRef !== ref) return;
      caseDetailView = acceptCaseDetail(value, ref);
    } catch (error) {
      if (token !== detailGeneration || expectedScope !== scope() || selectedCaseRef !== ref) return;
      caseDetailError = code(error);
    }
    if (token === detailGeneration) render();
  }

  async function loadLessonDetail(id) {
    const token = ++detailGeneration;
    selectedLessonId = id;
    selectedCaseRef = null;
    lessonDetailView = null;
    caseDetailView = null;
    lessonDetailError = null;
    caseDetailError = null;
    render();
    if (!permittedSession()) return;
    const expectedScope = scope();
    try {
      const value = await client.json(`/api/workspace/experience-lessons/heads/${encodeURIComponent(id)}`);
      if (token !== detailGeneration || expectedScope !== scope() || selectedLessonId !== id) return;
      lessonDetailView = acceptLessonDetail(value, id);
    } catch (error) {
      if (token !== detailGeneration || expectedScope !== scope() || selectedLessonId !== id) return;
      lessonDetailError = code(error);
    }
    if (token === detailGeneration) render();
  }

  async function loadEvaluation() {
    const token = ++eventGeneration;
    evaluationView = null;
    evaluationError = null;
    render();
    if (!permittedSession() || !eventId) return;
    const expectedEvent = eventId;
    const expectedScope = scope();
    try {
      const value = await client.json(`/api/workspace/finance-explanation/evaluations/${encodeURIComponent(expectedEvent)}`);
      if (token !== eventGeneration || expectedScope !== scope() || expectedEvent !== eventId) return;
      evaluationView = acceptEvaluation(value, expectedEvent);
    } catch (error) {
      if (token !== eventGeneration || expectedScope !== scope() || expectedEvent !== eventId) return;
      evaluationError = code(error);
    }
    if (token === eventGeneration) render();
  }

  async function loadCore(token) {
    try {
      if (!client.workspace()) await client.refreshWorkspaces();
      if (token !== generation || !permittedSession()) return;
      const expectedScope = scope();
      const results = await Promise.allSettled([
        client.json(CASES), client.json(LESSONS), client.json(HEAD),
        reviewerSession() ? client.json(CANDIDATES) : Promise.resolve(null),
      ]);
      if (token !== generation || expectedScope !== scope()) return;
      if (results[0].status === "fulfilled") {
        try { casePage = acceptCases(results[0].value); }
        catch (error) { caseError = code(error); }
      } else caseError = code(results[0].reason);
      if (results[1].status === "fulfilled") {
        try { lessonPage = acceptLessons(results[1].value); }
        catch (error) { lessonError = code(error); }
      } else lessonError = code(results[1].reason);
      if (results[2].status === "fulfilled") {
        try { head = acceptHead(results[2].value); }
        catch (error) { headError = code(error); }
      } else headError = code(results[2].reason);
      if (reviewerSession()) {
        if (results[3].status === "fulfilled") {
          try { candidatePage = acceptCandidates(results[3].value); }
          catch (error) { candidateError = code(error); }
        } else candidateError = code(results[3].reason);
      }
    } catch (error) {
      if (token !== generation) return;
      caseError = code(error);
      lessonError = code(error);
      headError = code(error);
      if (reviewerSession()) candidateError = code(error);
    } finally {
      if (token === generation) { loading = false; render(); }
    }
  }

  function reset({ reload = true, preserveEvent = false } = {}) {
    const token = ++generation;
    ++detailGeneration;
    ++eventGeneration;
    loading = reload && permittedSession();
    casePage = null; lessonPage = null; candidatePage = null; head = null; evaluationView = null;
    selectedCaseRef = null; selectedLessonId = null; caseDetailView = null; lessonDetailView = null;
    caseError = null; lessonError = null; candidateError = null;
    caseDetailError = null; lessonDetailError = null; headError = null; evaluationError = null;
    if (!preserveEvent) { activeEventId = null; eventId = null; }
    render();
    if (loading) {
      loadCore(token);
      if (eventId) loadEvaluation();
    }
  }

  function selectEvent(id) {
    const next = typeof id === "string" && eventPattern.test(id) ? id : null;
    if (next === eventId) return;
    eventId = next;
    loadEvaluation();
  }

  refresh.addEventListener("click", () => reset({ preserveEvent: true }));
  window.addEventListener("orgrebase:languagechange", render);
  window.addEventListener("orgrebase:sessionchange", () => reset());
  window.addEventListener("orgrebase:sessionended", () => reset({ reload: false }));
  window.addEventListener("orgrebase:workspacechange", () => reset());
  window.addEventListener("orgrebase:stateunavailable", () => reset({ reload: false }));
  window.addEventListener("orgrebase:staterendered", event => {
    activeEventId = event.detail?.active_event_id || null;
    selectEvent(activeEventId);
  });
  window.addEventListener("orgrebase:changeselection", () => {
    const selected = window.OrgRebaseChangeWorkbench?.selectedDetail()?.event?.event_id;
    selectEvent(selected || activeEventId);
  });
  window.addEventListener("pagehide", () => reset({ reload: false }));
  window.addEventListener("pageshow", event => { if (event.persisted) reset(); });
  window.OrgRebaseExperienceLibrary = Object.freeze({ refresh: () => reset({ preserveEvent: true }) });
  render();
  if (client.session()) reset();
  else client.refreshSession().catch(() => reset({ reload: false }));
})();
