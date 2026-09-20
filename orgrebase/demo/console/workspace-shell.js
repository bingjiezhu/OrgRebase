(() => {
  "use strict";

  const ROUTES = Object.freeze([
    { id: "onboarding", icon: "◎", order: "01", section: "primary" },
    { id: "quote", icon: "◆", order: "02", section: "primary" },
    { id: "assurance", icon: "◇", order: "03", section: "secondary" },
  ]);
  const ROUTE_ALIASES = Object.freeze({
    overview: "quote",
    materials: "onboarding",
    oac: "onboarding",
    "enterprise-input": "onboarding",
    data: "quote",
    "data-evolution": "quote",
    agents: "quote",
    acceptance: "quote",
    value: "quote",
    skills: "assurance",
    validation: "assurance",
    operations: "assurance",
  });
  const SUBROUTE_TABS = Object.freeze({
    overview: "work",
    quote: "work",
    data: "changes",
    "data-evolution": "changes",
    acceptance: "changes",
    value: "changes",
    agents: "collaboration",
    materials: "materials",
    "enterprise-input": "materials",
    oac: "oac",
    skills: "skills",
    validation: "validation",
    operations: "operations",
  });
  const STAGES = Object.freeze(["EMPTY", "CURRENT", "PREVIEWED", "APPROVED", "RECOVERY_REQUIRED"]);
  const COMPONENTS = Object.freeze(["DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"]);

  const COPY = Object.freeze({
    "zh-CN": Object.freeze({
      product: "企业工作平台",
      workspaceLabel: "当前变化事项",
      workspaceName: "蓝港报价持续演化案例",
      workspaceType: "企业报价 · 规则与版本管理",
      environment: "受控验证环境",
      operator: "报价运营负责人",
      lifecycleOnboarding: "企业接入",
      lifecycleOnboardingDetail: "低频 · 首次接入或契约变更",
      lifecycleDaily: "企业变化响应",
      lifecycleDailyDetail: "事件驱动 · 智能体自动修正，负责人精确审批",
      navigation: "一次接入 · 持续响应变化",
      workspaceNavigationAria: "OrgRebase 工作区导航",
      lifecycleNavigationAria: "工作区生命周期",
      sectionDaily: "变化处置",
      sectionDailyAssurance: "持续治理与保障",
      sectionPlatformValidation: "平台验证",
      sectionOnboarding: "企业接入",
      pages: {
        overview: ["变化处置", "规则变了，更新受影响的工作", "查看变化、影响范围和待审批事项，跟进正式版本更新。"],
        quote: ["变化处置", "上游规则变化后，更新受影响的工作", "从任务与团队协作，到影响预览、负责人审批和报价更新。"],
        onboarding: ["企业接入", "规则变了，先找出哪些工作需要更新", "本页用于首次接入：确认企业材料、规则与负责人。完成后进入「变化处置」，查看影响、审阅候选并批准必要更新。"],
        assurance: ["能力与保障", "管理团队能力、运行记录与服务状态", "查看Skill版本、任务记录、验证案例和运维信息。"],
        data: ["变化影响与后继版本", "审批前看候选影响，审批后看规范写入", "从已准入基线、最小上下文到零写预演、匹配负责人审批与后继报价版本，逐项解释重建、保留和人工复核。"],
        agents: ["基线组队与变化团队", "区分报价基线 v1 的原生协作和后续变更集投影", "展示基线实际拓扑、最小上下文交接、智能体的 Tool / Skill 与候选结果；变化轮只投影最小变化团队并复用已准入能力。"],
        acceptance: ["查看任务验收结果", "看懂做了什么、谁批准、最终写入什么", "只汇总本次运行已产生并校验的终态回执、人工决策与选择性重构结果，不借用独立测试档案。"],
        materials: ["接入数据", "企业第一次接入时提供什么", "展示长期企业材料如何进入安全投影；本次员工任务不混入接入数据。"],
        oac: ["OAC 组织契约", "把企业材料编译成长期运行契约", "接入智能体生成候选；指定负责人审阅准入，控制面保留最终权威。"],
        skills: ["能力中心", "管理智能体可使用和可演进的能力", "查看当前调用、已发布 Skill、评测与回滚边界，并对运行产生的经验候选做人工发布决策。"],
        validation: ["运行记录与验证", "查看任务结果与验证案例", "追溯当前任务，查看公开数据案例与独立可靠性验证。"],
        operations: ["运行保障", "企业运行所需的工程证据", "集中查看 OTLP、告警、留存、容量、恢复、部署、SBOM 与生产验证边界。"],
      },
      navState: { done: "已完成", current: "当前", ready: "可进入", waiting: "待进入", audit: "可审计", loading: "读取中", unavailable: "不可用" },
      pageKicker: "OrgRebase 企业工作区",
      termsTitle: "页面术语说明",
      termsOacTitle: "OAC · 组织智能体契约",
      termsOac: "Organizational Agent Contract：规定智能体可用的事实、权限、交接和验收规则；不是与客户签署的法律合同。",
      termsProjectionTitle: "安全投影与精确版本",
      termsProjection: "只提供当前身份获准查看、当前任务所需的字段；精确版本用来核对审批依据是否已变化。",
      termsAuthorityTitle: "候选、批准与生效",
      termsAuthority: "候选是待核验的建议。指定负责人批准精确提案后，控制面才能写入正式状态；AgentTeams 任务完成不等于业务已生效。",
      termsImpactTitle: "影响检查与选择性重构",
      termsImpact: "先检查变化影响哪些工作，再只更新必要部分。VMRC 是本次影响与处置依据的可核验记录；未知影响仍需核查。",
      taskContextSummary: "规则变化后，工作如何更新",
      sectionPrimary: "业务生命周期",
      sectionSecondary: "低频管理",
      lifecycleAria: "企业工作生命周期",
      lifecycleOnboardingStep: "企业接入",
      lifecycleOnboardingHint: "确认企业事实、责任人与准入规则",
      lifecycleQuoteStep: "变化驱动处置",
      lifecycleQuoteHint: "变化到达后，只组织受影响领域并生成后继版本",
      lifecycleAssuranceStep: "能力与保障",
      lifecycleAssuranceHint: "Skill、运行记录与运维",
      lifecycleDone: "已就绪",
      lifecycleCurrent: "当前阶段",
      lifecycleLocked: "等待前置",
      lifecycleAudit: "可审计",
      prerequisiteKicker: "变化处置前置条件",
      prerequisiteBlockedTitle: "企业组织契约尚未激活，当前不能建立可演化基线",
      prerequisiteBlockedBody: "请先完成企业材料接入、OAC 候选映射与契约校验，由指定负责人人工准入并激活运行时胶囊。系统可预分配运行身份，但激活前不能准入任务，也不能在该身份下执行。",
      prerequisiteOptionalBody: "本部署使用已配置的企业绑定，无需额外激活 OAC。任务说明只表达工作意图；启动时仍由服务端核验事实、权限与准入条件。",
      prerequisiteCheckingTitle: "正在确认企业接入条件",
      prerequisiteCheckingBody: "尚未取得当前工作区的有效准入状态，请刷新后重试。",
      prerequisiteReadyTitle: "企业基线已就绪，可以启动变化驱动案例",
      prerequisiteInUseTitle: "已准入的企业契约正在约束本次工作",
      prerequisiteBoundTitle: "已配置的企业绑定正在约束本次工作",
      prerequisiteInUseBody: "当前任务沿用已绑定的事实、权限与依赖；新的变化仍需核对其影响与负责人决定。",
      prerequisiteReadyBody: "任务使用已激活组织契约中的事实与权限。请填写工作说明并确认任务范围。",
      prerequisiteAction: "返回完成企业接入",
      executionPreviewKicker: "领域责任模型 · 启动前说明",
      executionPreviewTitle: "领域智能体自动完成候选；匹配的人工负责人只在本领域高风险写入前审批",
      executionPreviewAt: "产品智能体 ↔ 产品负责人",
      executionPreviewAtBody: "智能体自动读取产品事实并生成候选；负责人只审批产品规范变化",
      executionPreviewCapability: "法务智能体 ↔ 法务负责人",
      executionPreviewCapabilityBody: "智能体自动检查义务与限制；证据不足或法务规范变化时通知负责人",
      executionPreviewAuthority: "财务智能体 ↔ 财务负责人",
      executionPreviewAuthorityBody: "智能体自动补证定价与币种；只有匹配负责人能批准财务规范写入",
      executionPreviewFailure: "市场与商业化智能体 ↔ 市场与商业化负责人",
      executionPreviewFailureBody: "智能体使用受治理 Skill 组装候选；负责人保留对外口径与发布边界",
      executionPreviewBoundary: "智能体默认连续自动执行，只有精确权威边界才暂停并通知对应负责人；批准前规范写入为 0，批准后由控制面自动选择性重构。",
      processBaselineKicker: "现状人工流程 · 参考基线",
      processBaselineTitle: "八步现状流程与职责分工（RACI）：人工基线待企业校准，不是系统运行耗时",
      processBaselineBoundary: "表内时限仅作为参考人工基线，尚未在真实企业环境观测；系统实测结果只来自同运行回执，人工等待时间在审批门单独显示。",
      quoteTabsAria: "报价任务详情",
      onboardingTabsAria: "企业接入详情",
      assuranceTabsAria: "能力与保障详情",
      tabs: {
        work: "需求与当前交付",
        collaboration: "真实协作进度",
        changes: "业务数据演化与验收",
        materials: "企业材料",
        oac: "OAC 组织契约",
        skills: "Skill 能力",
        validation: "运行记录与验证",
        operations: "运行保障",
      },
      compassKicker: "30 秒变化摘要",
      compassTitle: "这个页面只回答五个问题",
      compassDemand: "什么发生变化",
      compassDemandWaiting: "先形成报价验证基线，再接收上游变化",
      compassDemandCurrent: "{change}：{from} → {to}",
      compassChangeLaunch: "产品上线日期",
      compassChangeCurrency: "报价币种",
      compassTeam: "谁在做",
      compassTeamBaselineValue: "报价基线 v1：产品 / 法务 / 财务 / 市场与商业化 + 审查智能体",
      compassTeamCurrentValue: "当前 {change} 变更集：{team}",
      compassTeamUnavailable: "当前变更集团队尚未观测",
      compassCapability: "用什么",
      compassCapabilityBaselineValue: "基线：HTTP 补证 Tool + quote-compose Skill",
      compassCapabilityCurrentValue: "当前轮能力引用：{skills} · {tools}",
      compassNoSkill: "无 Skill 版本回执",
      compassNoExternalTool: "无外部 Tool（仅提议）",
      compassApproval: "哪些负责人审批",
      compassApprovalWaiting: "尚无变更集：按领域匹配精确负责人",
      compassApprovalCurrent: "{owner}（{count}/1）",
      compassApprovalComplete: "已核对 {count} 次可见变更批准 · {owners}",
      compassApprovalNone: "尚无规则变更需要批准（0 次业务批准）",
      runBaseline: "报价已生成，等待规则变更",
      compassOwnerProduct: "产品负责人",
      compassOwnerFinance: "财务负责人",
      compassActors: {
        "change-coordinator": "协调器",
        "product-steward": "产品",
        "finance-steward": "财务",
        "gtm-steward": "市场与商业化",
      },
      compassDelivery: "交付什么",
      compassDeliveryWaiting: "等待形成内部报价工作稿",
      progressKicker: "本次真实运行 · 只显示已观测事件",
      progressTitle: "协作任务板 · AgentTeams、Tool、Skill 与人工决定",
      elementSync: "同步运行记录到 Element",
      elementOpen: "打开本次任务聊天室",
      elementBoundary: "聊天室由服务账号发布本次运行记录；审批仍在工作台完成。",
      elementUnavailable: "聊天室尚未配置",
      elementWaiting: "任务形成后可同步运行记录",
      elementReady: "可将本次运行记录同步到私有聊天室",
      elementPublishing: "正在同步并核对消息…",
      elementObserved: "已同步至报价 v{revision} · {time}",
      elementStale: "业务状态已有更新，请重新同步运行记录",
      elementFailed: "聊天室同步失败，请重试；业务执行与审批未受影响。",
      elementRequestFailed: "本次同步未完成，请确认操作权限或连接后重试。",
      elementConfigFailed: "观察服务配置不完整或无效，请管理员检查配置后重试。",
      elementConfigPrivate: "观察服务配置文件权限不符合要求，请管理员修正后重试。",
      elementSignInRequired: "登录已失效或尚未登录，请重新登录后重试同步。",
      elementPermissionDenied: "当前账号没有同步权限，请管理员核对本工作区的账号授权后重试。",
      elementPublisherCredentials: "观察服务账号认证失败，请管理员检查该账号的凭据后重试。",
      elementRoomPermissions: "聊天室访问受限，请管理员检查观察服务账号和私有房间权限后重试。",
      elementWorkspaceDenied: "观察服务未获准发布此工作区，请管理员核对允许的工作区后重试。",
      elementConnectionFailed: "聊天室连接或服务暂时不可用，请稍后重试；持续失败时请管理员检查网络和服务状态。",
      progressWaiting: "等待启动变化驱动案例",
      progressUnavailable: "暂时无法读取协作进度",
      progressRetrying: "进度连接暂时中断，正在重试（{attempt}/{total}）…",
      progressFollowPaused: "前端观察已暂停；后端可能仍在运行，可刷新页面或恢复跟随。",
      progressRetryStopped: "进度连接仍不可用；后端可能仍在运行，可恢复跟随。",
      progressResume: "恢复跟随",
      progressDetails: "运行明细 · {count} 个已观测动作",
      progressActionCaption: "任务初始形成 · AgentTeams 动作日志",
      progressActionColumns: ["序号", "工具", "动作", "操作标识", "状态"],
      progressNoActions: "尚无可核对的本次运行日志。",
      progressReplan: "重新规划",
      progressReviewEvidence: "本次运行：查看补证与复核",
      progressUsageCaption: "Reviewer 模型调用",
      progressUsageColumns: ["审查任务", "供应商 / 模型", "输入 Token", "输出 Token", "调用耗时", "测量来源"],
      progressUsagePhase: "第 {phase} 次审查",
      progressNoUsage: "本次运行尚无已核验的 Reviewer 模型回执。",
      progressUnknown: "未报告",
      progressFormationFailure: "初始任务形成失败原因：{code}；各变更的失败记录在对应提案中查看。",
      progressUsageSources: { provider_response: "供应商响应", client_receipt: "客户端调用回执", unavailable: "未报告" },
      progressUsageSource: "Token：{tokens}；耗时：{latency}",
      progressUsageBoundary: "仅展示这些 Reviewer 调用已报告的用量与耗时；缺失值不按零计算，不代表任务总耗时、账单或成本节约。",
      progressBoundary: "本轨直接读取同一运行已落盘动作和控制面状态；AgentTeams 已接收 ≠ 控制面已准入 ≠ 人工已批准 ≠ 规范状态已写入。",
      progressStatus: { WAITING: "等待发起", RUNNING: "真实执行中", ACTIVE: "业务待完成", COMPLETED: "已完成", FAILED: "执行失败" },
      progressStates: { OBSERVED: "已观测", PARTIAL: "部分观测", WAITING: "未观测" },
      progressMilestones: {
        TASK_INTAKE: "需求确认",
        PROJECT_CREATED: "创建项目",
        DELEGATED: "委派任务",
        ACKNOWLEDGED: "智能体接单",
        CONTEXT_AND_RESULT_HANDOFF: "上下文 / 结果交接",
        SUPPLEMENTAL_TOOL_EVIDENCE: "Tool 补证",
        SKILL_INVOKED: "Skill 调用",
        REVIEWER_ACCEPTED: "复核任务结果已接收",
        AGENTTEAMS_TERMINAL: "AgentTeams 终态",
        HUMAN_APPROVALS: "人工批准",
        CANONICAL_BUSINESS_TERMINAL: "业务终态",
      },
      currentStage: "当前阶段",
      currentRun: "当前运行",
      onboardingContext: "接入状态",
      onboardingIdentity: "契约激活身份",
      dailyAssuranceContext: "持续治理",
      dailyAssuranceIdentity: "与当前任务的关系",
      platformValidationContext: "平台验证",
      platformValidationIdentity: "与当前任务的关系",
      currentOrganization: "当前组织",
      onboardingWorkspaceType: "OAC 企业接入 · 受控试点",
      dailyAssuranceWorkspaceType: "企业持续工作 · 治理与保障",
      platformValidationWorkspaceType: "平台独立验证 · 不重复执行当前任务",
      dailyAssuranceFacts: {
        assurance: ["能力与保障", "本次任务记录与独立档案分开核对"],
        skills: ["能力治理", "当前调用 + 跨运行人工发布"],
        operations: ["工程运行证据", "当前运维状态 + 独立保障档案"],
      },
      platformValidationFacts: ["独立机制证据", "不重复执行当前报价任务"],
      acceptanceStoryKicker: "本次任务 · 从需求到可验收结果",
      acceptanceStoryTitle: "任务完成摘要",
      acceptanceStoryBody: "先看自动完成了什么、哪些变更经过人工批准，再查看最终工作结果与审计记录。",
      acceptanceStoryWaiting: "正在汇总本次运行…",
      acceptanceStepsWaiting: "等待本次任务的协作、批准与写入回执",
      acceptanceChangesWaiting: "完成后将逐项展示变更前后、负责人与批准结果",
      acceptanceFinalWaiting: "等待最终工作结果",
      acceptanceViewData: "查看字段变化",
      acceptanceDownloadQuote: "下载工作结果",
      acceptanceDownloadEvidence: "下载审计记录",
      acceptanceProcessDetails: "展开八步现状人工流程与职责分工（RACI，参考基线，非系统运行耗时）",
      onboardingPending: "等待 OAC 准入",
      onboardingReady: "组织契约已激活",
      onboardingOptional: "企业绑定已配置 · 无需额外 OAC 激活",
      onboardingIdentityOptional: "采用已配置企业绑定",
      onboardingConsumed: "组织契约已在任务中使用",
      onboardingIdentityPending: "等待激活摘要",
      onboardingIdentityBound: "已绑定当前组织与工作区",
      workspaceLoading: "正在读取权威状态",
      workspaceUnavailable: "工作区状态不可用",
      overviewProblem: "原本的问题",
      overviewProblemValue: "一个上游事实变化会让多个企业成果同时失效，人工难以证明哪些该改、哪些应保留",
      overviewSolution: "OrgRebase 的处理",
      overviewSolutionValue: "OAC 固定领域、事实与权威；报价基线 v1 先形成原生协作，变化轮再从持久收据投影最小变化团队并复用已准入能力",
      overviewResult: "最终结果",
      overviewResultValue: "领域智能体自动修正候选，匹配的人工负责人审批后，控制面只写必要后继版本",
      overviewFlowOnboarding: ["企业材料", "候选映射", "人工准入", "组织契约激活"],
      overviewFlowDaily: ["上游变化", "冻结变更集", "影响发现", "受影响智能体自动修正", "负责人精确审批", "选择性重构"],
      openMaterials: "继续企业接入",
      openOac: "继续组织契约适配",
      viewActiveOac: "查看已激活组织契约",
      openWorkItem: "发起工作需求",
      continueWorkItem: "继续当前工作事项",
      viewCompletedWork: "查看已完成工作事项",
      materialsKicker: "企业材料成熟度 · 等待服务端证据",
      materialsTitle: "企业提供五类长期材料，系统编译为组织契约",
      materialsBody: "浏览器只读取后端已准入的安全投影；材料是受控合成样例还是其他数据类型，严格依据服务端数据分类与合成标记展示。本次员工任务与原始私密值均不混入接入材料。",
      materialClassSynthetic: "组织背景：受控企业样例 · 仅验证结构闭环",
      materialPricingTitle: "商品输入与计价规则分别核对",
      materialBasketInput: "商品输入",
      materialPolicyInput: "金额规则",
      materialBasketCount: "{count} 条商品明细 · {currency}",
      materialPolicyRates: "整单折扣 {discount}% · 价外税 {tax}%",
      materialPricingScope: "下列来源来自已准入字段；组织身份不代表原交易企业，来源真实性以相应清单与授权为准。",
      materialPricingSources: "查看两类输入各自的来源",
      materialClassObserved: "服务端已声明数据类型：{dataClass}",
      materialClassUnclassified: "数据类型未观测 · 不推断材料成熟度",
      materialClassUnavailable: "材料状态不可用 · 不展示为已加载",
      materialStatusReady: "来源已准入并与运行配置匹配",
      materialStatusWaiting: "等待安全输入投影",
      pack: "企业材料包",
      organization: "接入组织",
      profile: "组织配置",
      actor: "任务发起人",
      customer: "目标客户",
      purpose: "任务目的",
      deliverable: "预期交付物",
      template: "输出模板",
      fiveRoots: "五类 OAC 根材料",
      facts: "已准入组织事实",
      factsCount: "{count} 项当前会话可见的安全投影字段",
      rootCount: "{count}/5 类材料已装载",
      inputToOutput: "从企业输入到可执行工作区",
      flowSource: "企业材料",
      flowSourceDetail: "事实、来源、权威、敏感级别",
      flowOac: "OAC 契约",
      flowOacDetail: "领域、知识、权限、能力、依赖",
      flowContext: "人工准入",
      flowContextDetail: "精确摘要、未知项与指定负责人",
      flowOutcome: "组织契约激活",
      flowOutcomeDetail: "后续任务持续受权限、能力和上下文边界约束",
      loadedByConfig: "由启动配置安全装载",
      exactVersionBound: "已绑定精确版本",
      materialPackName: "当前企业材料包",
      materialOrganizationName: "当前接入组织",
      componentComplete: "完整",
      componentSource: "{component}材料来源",
      sourceRegistered: "{domain}数据源已登记",
      authorityMatched: "{domain}权威负责人已匹配",
      domainNames: { product: "产品", legal: "法务", finance: "财务", gtm: "市场与商业化" },
      noSource: "当前服务尚未返回企业材料安全投影；页面不会补造输入。",
      noValue: "未提供",
      source: "来源",
      authority: "权威",
      sensitivity: "敏感级别",
      completeness: "完整性",
      digest: "摘要",
      componentNames: { DOMAIN: "领域", KNOWLEDGE: "知识", AUTHORITY: "权威", CAPABILITY: "能力", DEPENDENCY: "依赖" },
      slotNames: {
        product_plan: "企业方案", launch_date: "上线日期", data_residency: "数据驻留",
        notice_required: "客户通知要求", price_band: "价格带", currency: "报价币种",
        pricing_policy: "报价计算规则", quote_basket: "报价商品明细",
        partner_terms: "合作条款", quote_compose_skill: "报价组装 Skill", public_message: "对外口径",
      },
      sensitivityNames: { PUBLIC: "公开", INTERNAL: "内部", CONFIDENTIAL: "机密", RESTRICTED: "受限" },
      materialValueNames: {
        USD: "美元（USD）",
        "US and EU regions supported": "支持美国与欧盟区域驻留",
        "legal-review": "需要法务复核",
        strategic: "战略客户定价",
        "Evergreen Enterprise Plus": "常青企业增强版",
        "The enterprise launch remains on the approved schedule.": "企业版上线计划仍按已批准时间执行",
        "skill:enterprise-quote-compose@1.0": "企业报价组装 Skill",
      },
      materialPackNames: { "pack:evergreen-enterprise-quote@r1": "常青工业企业报价接入包" },
      organizationNames: { "org:evergreen-industries": "常青工业" },
      customerNames: { "customer:blue-harbor": "蓝港客户" },
      routeUnavailable: "页面不可用",
      routeUnavailableBody: "页面组件未能加载，请刷新后重试。",
      routeInvalid: "链接中的页面不存在，已返回企业接入。请从左侧导航继续；现有工作没有改变。",
      routeNext: "下一页",
      stageNames: {
        EMPTY: "等待员工提出需求", CURRENT: "当前成果", PREVIEWED: "变更待审批", APPROVED: "变更已批准 · 待应用",
        RECOVERY_REQUIRED: "变更已生效 · 待恢复结果记录",
      },
      runPending: "业务运行待形成",
      runAwaitingEmployee: "等待员工发起工作",
      runCandidatePending: "员工确认后启动业务运行",
      runActive: "业务运行进行中",
      runRecorded: "成果已生成，正在核对完成记录",
      runComplete: "当前任务已完成",
      taskCandidateStage: "需求候选待确认",
      caseBadge: "当前事项",
      taskIntakeKicker: "新建工作 · 企业报价",
      taskIntakeTitle: "说明需求，交给团队生成报价",
      taskIntakeBody: "客户、业务事实和权限来自已准入组织契约。确认工作范围后启动团队；后续规则变更将提交对应负责人审批。",
      taskCompletedKicker: "员工发起记录 · 已绑定当前运行",
      taskCompletedTitle: "员工需求已确认",
      taskCompletedBody: "本次工作说明已经校验并启动智能体团队；客户、事实与权限始终来自已准入组织契约。",
      taskUnverifiedKicker: "已有报价 · 发起记录未核验",
      taskUnverifiedTitle: "已有报价，任务发起记录未核验",
      taskPromptLabel: "基线工作说明（必填，至少 8 个字符）",
      taskPromptExample: "请为蓝港客户生成一份符合现行产品、法务、财务、市场与商业化规则的企业报价。",
      taskPrivateTitle: "员工提交的工作说明",
      taskPrivateScope: "仅当前任务发起人可见 · 不进入公开证据、事件或观测数据",
      taskPrivateLoading: "正在读取本次任务的私有工作说明……",
      taskPrivateNotRetained: "此运行未保存工作说明原文。",
      taskPrivateRestricted: "仅任务发起人可查看工作说明原文。",
      taskPrivateUnavailable: "暂时无法读取工作说明，请稍后重试。",
      taskActorLabel: "发起人",
      taskCustomerLabel: "客户",
      taskKindLabel: "工作类型",
      taskKindValue: "企业报价",
      taskContractLabel: "组织契约",
      taskContractReady: "已就绪 · 启动时由服务端校验",
      taskContractBlocked: "企业接入尚未完成",
      taskCompleteOnboarding: "先完成企业接入",
      taskPrepare: "校验工作说明并生成候选",
      taskPreparing: "正在校验并生成受约束候选…",
      taskAdmit: "确认范围并准入任务",
      taskAdmitting: "正在绑定员工确认…",
      taskAdmittedButton: "任务已准入",
      taskRun: "启动智能体团队",
      taskRunning: "正在启动智能体团队…",
      taskCandidateTitle: "受约束任务候选",
      taskCandidateReady: "等待员工确认",
      taskCandidateHold: "已停在人工处理队列",
      taskTemplateLabel: "采用模板",
      taskTemplateValue: "企业报价标准模板",
      taskDigestLabel: "启动证据",
      taskChainProof: "员工候选已确认 · 组织契约已校验 · 智能体团队已启动",
      taskCandidateProof: "候选已生成 · 等待员工确认",
      taskHoldProof: "工作意图校验未通过 · 未启动智能体团队",
      taskAdmittedProof: "员工确认已绑定 · 等待启动智能体团队",
      taskAdmittedRetryProof: "员工确认已绑定 · 上次启动未完成，可安全重试",
      taskConstraintLabel: "契约约束",
      taskStartPrerequisitesLabel: "启动前置条件",
      taskHoldReasonLabel: "待处理原因",
      taskNoUnknowns: "组织契约与任务范围已校验 · 可启动当前任务",
      taskUnknownFallback: "服务端返回未决项，需要人工核对",
      taskInputProof: "工作说明摘要已绑定 · {length} 个字符",
      taskConfirm: "确认范围并准入任务",
      taskAdmitted: "员工已确认 · 等待显式启动",
      taskAdmittedRetry: "员工已确认 · 启动未完成，可安全重试",
      taskStarting: "正在校验组织契约并启动任务…",
      taskStarted: "员工需求已确认，智能体团队已启动",
      taskStartedBadge: "协作工作稿已形成",
      taskEvidenceUnavailable: "任务发起证据不可用",
      taskEvidenceUnavailableDetail: "报价已形成，但未观测到与当前运行绑定的持久化任务发起收据；界面不推断员工已确认。",
      taskOacBound: "OAC 组织契约已精确绑定",
      taskOacOptional: "本部署未强制 OAC 激活绑定",
      taskBoundary: "工作说明不授予权限、不进入领域事实，也不会绕过后续产品与财务人工批准。当前已验证版本只处理企业报价任务。",
      taskError: "需求处理失败，请检查输入或企业契约状态。",
      taskIdentityError: "当前操作者与组织配置中的任务发起人不一致。",
      taskContractError: "OAC 组织契约尚未满足当前任务的启动条件。",
      taskReasonNames: {
        PROMPT_REQUIRED_OR_TOO_SHORT: "需求说明至少需要 8 个字符",
        PROMPT_TASK_INTENT_MISMATCH: "工作说明与当前已准入的企业报价任务不匹配",
        TASK_ACTOR_OUT_OF_PROFILE_SCOPE: "发起人不在当前组织配置范围内",
        CUSTOMER_OUT_OF_PROFILE_SCOPE: "客户不在当前已准入的任务范围内",
        DELIVERABLE_KIND_OUT_OF_PROFILE_SCOPE: "交付物类型未被当前模板准入",
        PROMPT_AUTHORITY_OVERRIDE_REQUESTED: "需求中存在绕过人工批准或权限的指令",
        OAC_ADAPTATION_EXACT_ENTERPRISE_PACK_REQUIRED: "OAC 企业材料尚未完成精确绑定",
        OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED: "OAC 激活绑定尚未完成",
      },
      taskUnknownNames: {
        task_intent: "任务目标",
        admitted_task_actor: "已准入发起人",
        admitted_customer: "已准入客户",
        admitted_deliverable_kind: "已准入交付物",
        requested_authority: "请求的权限",
        oac_activation_binding: "OAC 激活绑定",
      },
      skillCenterKicker: "跨运行能力治理",
      skillCenterTitle: "智能体使用受版本管理的能力，经验只能经人审批后发布",
      skillCenterBody: "本页不参与当前报价决策。它把真实调用、已发布 Skill 与终态运行归纳的经验候选分开，防止智能体未经治理自动改写能力。",
      skillCenterBadge: "Skill 治理",
      skillCurrentTitle: "本次任务的能力调用",
      skillCurrentBoundary: "只在同一运行的智能体、Skill 活动行与回执精确匹配后显示",
      skillCurrentAgent: "调用智能体",
      skillCurrentCapability: "能力与版本",
      skillCurrentPurpose: "为什么调用",
      skillCurrentEffect: "结果与权威边界",
      skillCurrentWaiting: "等待当前任务产生 Skill 调用回执",
      skillPublishedTitle: "已发布能力",
      skillPublishedBoundary: "可发现 · 精确版本 · 评测后发布",
      skillExperienceTitle: "经验候选审阅",
      skillExperienceBoundary: "运行经验只是候选；人工决策是 Skill 发布批准，不是业务结果批准",
      skillLifecycle: "完成运行 → 经验候选 → 独立评测 → 人工决策 → 受控灰度试调用",
      publicEvidence: "附加验证 · BPI 2019",
      publicEvidenceDetail: "公开真实流程，不冒充报价数据",
    }),
    en: Object.freeze({
      product: "Enterprise Work Platform",
      workspaceLabel: "CURRENT CHANGE CASE",
      workspaceName: "Blue Harbor Quote Evolution Case",
      workspaceType: "Enterprise quoting · Rules and version management",
      environment: "Controlled validation environment",
      operator: "Quote Operations Owner",
      lifecycleOnboarding: "Enterprise Onboarding",
      lifecycleOnboardingDetail: "LOW FREQUENCY · FIRST SETUP OR CONTRACT CHANGE",
      lifecycleDaily: "Enterprise Change Response",
      lifecycleDailyDetail: "EVENT DRIVEN · AGENTS REPAIR, OWNERS APPROVE",
      navigation: "ONBOARD ONCE · RESPOND TO CHANGE CONTINUOUSLY",
      workspaceNavigationAria: "OrgRebase workspace navigation",
      lifecycleNavigationAria: "Workspace lifecycle",
      sectionDaily: "CHANGE RESPONSE",
      sectionDailyAssurance: "CONTINUOUS GOVERNANCE & ASSURANCE",
      sectionPlatformValidation: "PLATFORM VALIDATION",
      sectionOnboarding: "ENTERPRISE ONBOARDING",
      pages: {
        overview: ["Change Response", "Update work affected by changing rules", "Review changes, affected scope, and pending decisions, then track version updates."],
        quote: ["Change Response", "Update work affected by upstream rules", "Follow the task and team collaboration through impact preview, owner approval, and Quote updates."],
        onboarding: ["Enterprise Onboarding", "When rules change, identify the work that needs updating", "First-time setup: confirm enterprise inputs, rules and responsible owners. Then open Change Response to inspect impact, review candidates and approve the necessary updates."],
        assurance: ["Capabilities & Assurance", "Manage team capabilities, run records, and service health", "Browse Skill versions, task records, verification cases, and operations."],
        data: ["Change Impact & Successor Versions", "See candidate impact before approval and canonical writes after approval", "Trace the admitted baseline and least-privilege context through zero-write Preview, matching-Owner approval, and successor Quote versions, including rebuild, preserve, and human-review decisions."],
        agents: ["Baseline Formation & Change Team", "Separate Quote v1 native collaboration from later ChangeSet projections", "Inspect the baseline topology, least-privilege handoffs, Agent Tool / Skill calls, and candidate results; change rounds only project the minimum change_team and reuse admitted capabilities."],
        acceptance: ["View Task Acceptance Result", "Understand what changed, who approved it, and what was written", "Summarizes only this run's verified terminal receipts, human decisions, and selective-Rebase result without borrowing from independent test archives."],
        materials: ["Onboarding Data", "What the enterprise provides once", "Shows how long-lived enterprise inputs enter a safe projection; the current employee task is not mixed into onboarding data."],
        oac: ["OAC Organization Contract", "Compile enterprise inputs into a durable runtime contract", "The Intake Agent proposes; the designated owner reviews admission, while the control plane retains authority."],
        skills: ["Capability Center", "Govern the capabilities Agents can use and evolve", "Review current invocations, released Skills, evaluation and rollback boundaries, then make human release decisions for experience candidates."],
        validation: ["Run records & validation", "Review task results and verification cases", "Trace the current task, explore public-data cases, and inspect independent reliability checks."],
        operations: ["Runtime Assurance", "Engineering evidence for enterprise operation", "Review OTLP, alerts, retention, capacity, recovery, deployment, SBOM, and production-validation boundaries."],
      },
      navState: { done: "DONE", current: "CURRENT", ready: "READY", waiting: "UP NEXT", audit: "AUDIT", loading: "LOADING", unavailable: "UNAVAILABLE" },
      pageKicker: "ORGREBASE ENTERPRISE WORKSPACE",
      termsTitle: "Page terminology",
      termsOacTitle: "OAC · Organizational Agent Contract",
      termsOac: "Rules for the facts, authority, handoffs and acceptance available to Agents. This is a system contract, not a legal agreement with a customer.",
      termsProjectionTitle: "Safe projection and exact versions",
      termsProjection: "Only task-relevant fields authorized for the current identity are disclosed. Exact versions detect whether the basis of approval has changed.",
      termsAuthorityTitle: "Candidate, approval and application",
      termsAuthority: "A candidate awaits verification. The control plane writes official state only after the designated owner approves the exact proposal. AgentTeams task completion is not business application.",
      termsImpactTitle: "Impact check and selective rebase",
      termsImpact: "Check affected work, then update only what is necessary. VMRC records the verifiable impact and disposition basis; unknown impact still needs review.",
      taskContextSummary: "How work updates when rules change",
      sectionPrimary: "BUSINESS LIFECYCLE",
      sectionSecondary: "LOW-FREQUENCY ADMIN",
      lifecycleAria: "Enterprise work lifecycle",
      lifecycleOnboardingStep: "Enterprise onboarding",
      lifecycleOnboardingHint: "Confirm enterprise facts, owners, and admission rules",
      lifecycleQuoteStep: "Change-driven response",
      lifecycleQuoteHint: "On change arrival, form only affected domains and produce successor versions",
      lifecycleAssuranceStep: "Capabilities & assurance",
      lifecycleAssuranceHint: "Skills, run records, and operations",
      lifecycleDone: "READY",
      lifecycleCurrent: "CURRENT",
      lifecycleLocked: "PREREQUISITE PENDING",
      lifecycleAudit: "AUDITABLE",
      prerequisiteKicker: "CHANGE-RESPONSE PREREQUISITE",
      prerequisiteBlockedTitle: "The organization contract is not active; an evolvable baseline cannot be formed",
      prerequisiteBlockedBody: "Complete enterprise-material intake, OAC candidate mapping, contract validation, designated-owner admission, and Runtime Capsule activation first. A run identity may be preallocated, but no Task can be admitted or executed under it before activation.",
      prerequisiteOptionalBody: "This deployment uses its configured enterprise binding and needs no additional OAC activation. Task descriptions express intent only; the server still checks facts, authority, and admission at execution.",
      prerequisiteCheckingTitle: "Checking enterprise admission conditions",
      prerequisiteCheckingBody: "A valid admission state for this workspace is not available yet. Refresh and retry.",
      prerequisiteReadyTitle: "The enterprise baseline is ready; the change-driven case can start",
      prerequisiteInUseTitle: "The admitted enterprise contract governs this task",
      prerequisiteBoundTitle: "The configured enterprise binding governs this task",
      prerequisiteInUseBody: "This task uses its bound facts, authority and dependencies. New changes still require impact checks and the responsible owner’s decision.",
      prerequisiteReadyBody: "The task uses facts and permissions from the active organization contract. Enter the work description and confirm its scope.",
      prerequisiteAction: "Return to enterprise onboarding",
      executionPreviewKicker: "DOMAIN RESPONSIBILITY MODEL · BEFORE START",
      executionPreviewTitle: "Domain Agents produce candidates automatically; matching Human Owners approve only high-risk writes in their domain",
      executionPreviewAt: "Product Agent ↔ Product Owner",
      executionPreviewAtBody: "The Agent reads product facts and proposes repairs; the Owner approves only Product canonical changes",
      executionPreviewCapability: "Legal Agent ↔ Legal Owner",
      executionPreviewCapabilityBody: "The Agent checks obligations and constraints; insufficient evidence or Legal canonical change notifies the Owner",
      executionPreviewAuthority: "Finance Agent ↔ Finance Owner",
      executionPreviewAuthorityBody: "The Agent recovers pricing and currency evidence; only the matching Owner may approve Finance canonical writes",
      executionPreviewFailure: "GTM Agent ↔ GTM Owner",
      executionPreviewFailureBody: "The Agent uses a governed Skill to compose candidates; the Owner retains messaging and release authority",
      executionPreviewBoundary: "Agents run continuously by default. Only an exact authority boundary pauses and notifies the matching Owner: 0 canonical writes before approval, then automatic selective Rebase by the control plane.",
      processBaselineKicker: "AS-IS HUMAN PROCESS · REFERENCE BASELINE",
      processBaselineTitle: "Eight-step current-state process and RACI: human baseline pending enterprise calibration, not system runtime",
      processBaselineBoundary: "Timings in this table are reference human baselines not yet observed in a real enterprise. System measurements come only from same-run receipts; human waiting time is shown separately at approval gates.",
      quoteTabsAria: "Quote task details",
      onboardingTabsAria: "Enterprise onboarding details",
      assuranceTabsAria: "Capabilities and assurance details",
      tabs: {
        work: "Request & Current Output",
        collaboration: "Observed Collaboration",
        changes: "Data Evolution & Acceptance",
        materials: "Enterprise Inputs",
        oac: "OAC Organization Contract",
        skills: "Skill Capabilities",
        validation: "Run records & validation",
        operations: "Runtime Assurance",
      },
      compassKicker: "30-SECOND CHANGE SUMMARY",
      compassTitle: "This page answers only five questions",
      compassDemand: "What changed?",
      compassDemandWaiting: "Form the Quote validation baseline, then receive an upstream change",
      compassDemandCurrent: "{change}: {from} → {to}",
      compassChangeLaunch: "Product launch date",
      compassChangeCurrency: "Quote currency",
      compassTeam: "Who is doing it?",
      compassTeamBaselineValue: "Quote v1 baseline: Product / Legal / Finance / GTM + Reviewer",
      compassTeamCurrentValue: "Current {change} ChangeSet: {team}",
      compassTeamUnavailable: "Current ChangeSet team not observed",
      compassCapability: "What do they use?",
      compassCapabilityBaselineValue: "Baseline: HTTP evidence Tool + quote-compose Skill",
      compassCapabilityCurrentValue: "Current-round capability refs: {skills} · {tools}",
      compassNoSkill: "No Skill version receipt",
      compassNoExternalTool: "No external Tool (proposal-only)",
      compassApproval: "Which Owners approve?",
      compassApprovalWaiting: "No ChangeSet yet: match the exact domain Owner",
      compassApprovalCurrent: "{owner} ({count}/1)",
      compassApprovalComplete: "{count} visible change approvals verified · {owners}",
      compassApprovalNone: "No rule change needs approval (0 business approvals)",
      runBaseline: "Quote created; awaiting rule changes",
      compassOwnerProduct: "Product Owner",
      compassOwnerFinance: "Finance Owner",
      compassActors: {
        "change-coordinator": "Coordinator",
        "product-steward": "Product",
        "finance-steward": "Finance",
        "gtm-steward": "GTM",
      },
      compassDelivery: "What is delivered?",
      compassDeliveryWaiting: "Waiting for the internal quote working draft",
      progressKicker: "CURRENT OBSERVED RUN · NO SYNTHETIC PROGRESS",
      progressTitle: "Collaboration task board · AgentTeams, Tools, Skills and human decisions",
      elementSync: "Sync run records to Element",
      elementOpen: "Open this task's room",
      elementBoundary: "A service account publishes this run's records. Approvals remain in the workspace.",
      elementUnavailable: "Chatroom is not configured",
      elementWaiting: "Run records become available after task formation",
      elementReady: "This run can be shared with its private observation room",
      elementPublishing: "Syncing and verifying the message…",
      elementObserved: "Synced through quote v{revision} · {time}",
      elementStale: "Business state has changed. Sync the updated run record.",
      elementFailed: "Chat sync failed. Retry when available; execution and approvals are unchanged.",
      elementRequestFailed: "This sync did not complete. Check your permissions or connection, then retry.",
      elementConfigFailed: "Observer configuration is incomplete or invalid. Ask an administrator to check it, then retry.",
      elementConfigPrivate: "Observer configuration file permissions are invalid. Ask an administrator to correct them, then retry.",
      elementSignInRequired: "Your session is missing or expired. Sign in again, then retry the sync.",
      elementPermissionDenied: "Your account cannot sync this workspace. Ask an administrator to check its authorization, then retry.",
      elementPublisherCredentials: "Observer account authentication failed. Ask an administrator to check its credentials, then retry.",
      elementRoomPermissions: "Room access is restricted. Ask an administrator to check the observer account and private room permissions, then retry.",
      elementWorkspaceDenied: "The observer is not allowed to publish this workspace. Ask an administrator to check the allowed workspaces, then retry.",
      elementConnectionFailed: "The chat connection or service is temporarily unavailable. Retry shortly; if it persists, ask an administrator to check the network and service.",
      progressWaiting: "Waiting to start the change-driven case",
      progressUnavailable: "Progress evidence is unavailable; no process is reconstructed",
      progressRetrying: "The progress connection was interrupted; retrying ({attempt}/{total})…",
      progressFollowPaused: "Browser observation is paused; the backend may still be running. Refresh the page or resume following.",
      progressRetryStopped: "The progress connection is still unavailable; the backend may still be running. Resume following to try again.",
      progressResume: "Resume following",
      progressDetails: "Run details · {count} observed actions",
      progressActionCaption: "Task formation · AgentTeams action journal",
      progressActionColumns: ["Sequence", "Tool", "Action", "Operation key", "Status"],
      progressNoActions: "No verifiable action journal is available for this run yet.",
      progressReplan: "Replanning",
      progressReviewEvidence: "This run: view evidence recovery and review",
      progressUsageCaption: "Reviewer model calls",
      progressUsageColumns: ["Review task", "Provider / model", "Input tokens", "Output tokens", "Call duration", "Measurement source"],
      progressUsagePhase: "Review {phase}",
      progressNoUsage: "No verified Reviewer model receipts are available for this run yet.",
      progressUnknown: "Not reported",
      progressFormationFailure: "Initial task formation failed: {code}. Change-specific failures are recorded on the corresponding proposal.",
      progressUsageSources: { provider_response: "Provider response", client_receipt: "Client call receipt", unavailable: "Not reported" },
      progressUsageSource: "Tokens: {tokens}; duration: {latency}",
      progressUsageBoundary: "Reported usage and duration cover these Reviewer calls only. Missing values are not zero; these records do not establish total task duration, a bill, or cost savings.",
      progressBoundary: "This rail reads persisted actions and control-plane state from the same run. AgentTeams received ≠ control admitted ≠ human approved ≠ canonical applied.",
      progressStatus: { WAITING: "WAITING TO START", RUNNING: "RUNNING ON OBSERVED EVENTS", ACTIVE: "BUSINESS WORK PENDING", COMPLETED: "COMPLETED", FAILED: "EXECUTION FAILED" },
      progressStates: { OBSERVED: "OBSERVED", PARTIAL: "PARTIAL", WAITING: "NOT OBSERVED" },
      progressMilestones: {
        TASK_INTAKE: "Request confirmed",
        PROJECT_CREATED: "Project created",
        DELEGATED: "Tasks delegated",
        ACKNOWLEDGED: "Agents acknowledged",
        CONTEXT_AND_RESULT_HANDOFF: "Context / result handoff",
        SUPPLEMENTAL_TOOL_EVIDENCE: "Tool evidence",
        SKILL_INVOKED: "Skill invoked",
        REVIEWER_ACCEPTED: "Review task result received",
        AGENTTEAMS_TERMINAL: "AgentTeams terminal",
        HUMAN_APPROVALS: "Human approvals",
        CANONICAL_BUSINESS_TERMINAL: "Business terminal",
      },
      currentStage: "CURRENT STAGE",
      currentRun: "CURRENT RUN",
      onboardingContext: "ONBOARDING STATUS",
      onboardingIdentity: "CONTRACT ACTIVATION IDENTITY",
      dailyAssuranceContext: "CONTINUOUS GOVERNANCE",
      dailyAssuranceIdentity: "RELATION TO CURRENT WORK ITEM",
      platformValidationContext: "PLATFORM VALIDATION",
      platformValidationIdentity: "RELATION TO CURRENT WORK ITEM",
      currentOrganization: "CURRENT ORGANIZATION",
      onboardingWorkspaceType: "OAC enterprise onboarding · controlled pilot",
      dailyAssuranceWorkspaceType: "Enterprise continuous work · governance & assurance",
      platformValidationWorkspaceType: "Independent platform validation · does not re-run the current task",
      dailyAssuranceFacts: {
        assurance: ["CAPABILITIES & ASSURANCE", "Review this task and independent archives separately"],
        skills: ["CAPABILITY GOVERNANCE", "Current invocation + cross-run human release"],
        operations: ["ENGINEERING RUNTIME EVIDENCE", "Current operational state + independent assurance archives"],
      },
      platformValidationFacts: ["INDEPENDENT MECHANISM EVIDENCE", "Does not execute the current Quote task again"],
      acceptanceStoryKicker: "THIS TASK · FROM REQUEST TO ACCEPTABLE RESULT",
      acceptanceStoryTitle: "Task Completion Summary",
      acceptanceStoryBody: "See what automation completed and which changes required human approval, then inspect the final work result and audit record.",
      acceptanceStoryWaiting: "Summarizing this run…",
      acceptanceStepsWaiting: "Waiting for collaboration, approval, and write receipts from this task",
      acceptanceChangesWaiting: "Once complete, each before/after change, owner, and approval result will appear here",
      acceptanceFinalWaiting: "Waiting for the final work result",
      acceptanceViewData: "View Field Changes",
      acceptanceDownloadQuote: "Download Work Result",
      acceptanceDownloadEvidence: "Download Audit Record",
      acceptanceProcessDetails: "Expand the AS-IS eight-step human process and RACI (reference baseline, not system runtime)",
      onboardingPending: "Awaiting OAC admission",
      onboardingReady: "Organization contract active",
      onboardingOptional: "Enterprise binding configured · no additional OAC activation needed",
      onboardingIdentityOptional: "Configured enterprise binding",
      onboardingConsumed: "Organization contract consumed by a task",
      onboardingIdentityPending: "Awaiting activation digest",
      onboardingIdentityBound: "Bound to this organization and Workspace",
      workspaceLoading: "Loading authoritative Workspace state",
      workspaceUnavailable: "Workspace state unavailable",
      overviewProblem: "THE ORIGINAL PROBLEM",
      overviewProblemValue: "One upstream fact can invalidate several enterprise outputs, while people cannot prove what must change and what should stay",
      overviewSolution: "HOW ORGREBASE HANDLES IT",
      overviewSolutionValue: "OAC fixes domains, facts, and authority; Quote v1 first forms the native baseline, then each change projects a minimum change_team from durable receipts and reuses admitted capabilities",
      overviewResult: "THE OUTCOME",
      overviewResultValue: "Domain Agents propose repairs automatically; matching Human Owners approve, then the control plane writes only necessary successors",
      overviewFlowOnboarding: ["Enterprise inputs", "Candidate mapping", "Owner admission", "Organization contract active"],
      overviewFlowDaily: ["Upstream change", "Frozen ChangeSet", "Impact discovery", "Affected Agents repair", "Exact Owner approval", "Selective Rebase"],
      openMaterials: "Continue onboarding",
      openOac: "Continue to organization-contract adaptation",
      viewActiveOac: "View active organization contract",
      openWorkItem: "Start a work request",
      continueWorkItem: "Continue current work item",
      viewCompletedWork: "View completed work item",
      materialsKicker: "ENTERPRISE INPUT MATURITY · AWAITING SERVER EVIDENCE",
      materialsTitle: "Five long-lived input classes compile into one organization contract",
      materialsBody: "The browser reads only an admitted safe projection. It presents the source classification and whether the inputs are controlled synthetic samples exactly as declared by the server. Current employee work and raw private values never enter onboarding materials.",
      materialClassSynthetic: "ORGANIZATION: CONTROLLED ENTERPRISE SAMPLE · STRUCTURAL LOOP ONLY",
      materialPricingTitle: "Inspect product inputs and pricing rules separately",
      materialBasketInput: "Product inputs",
      materialPolicyInput: "Amount rules",
      materialBasketCount: "{count} product lines · {currency}",
      materialPolicyRates: "Order discount {discount}% · exclusive tax {tax}%",
      materialPricingScope: "These sources come from admitted fields. The organization is not asserted to be the original retailer; verify provenance and permissions against the corresponding manifests.",
      materialPricingSources: "Inspect each input's source",
      materialClassObserved: "SERVER-DECLARED DATA CLASS: {dataClass}",
      materialClassUnclassified: "DATA CLASS NOT OBSERVED · MATURITY IS NOT INFERRED",
      materialClassUnavailable: "INPUT STATE UNAVAILABLE · NOT SHOWN AS LOADED",
      materialStatusReady: "Source admitted and matched to runtime configuration",
      materialStatusWaiting: "Awaiting safe input projection",
      pack: "ENTERPRISE INPUT PACK",
      organization: "ONBOARDED ORGANIZATION",
      profile: "ORGANIZATION PROFILE",
      actor: "TASK ACTOR",
      customer: "TARGET CUSTOMER",
      purpose: "PURPOSE",
      deliverable: "DELIVERABLE",
      template: "OUTPUT TEMPLATE",
      fiveRoots: "FIVE OAC ROOT INPUTS",
      facts: "ADMITTED ORGANIZATION FACTS",
      factsCount: "{count} safe-projection fields visible in this session",
      rootCount: "{count}/5 input classes loaded",
      inputToOutput: "FROM ENTERPRISE INPUT TO EXECUTABLE WORKSPACE",
      flowSource: "Enterprise inputs", flowSourceDetail: "facts, sources, authority, sensitivity",
      flowOac: "OAC contract", flowOacDetail: "domain, knowledge, authority, capability, dependency",
      flowContext: "Owner admission", flowContextDetail: "exact summary, Unknowns, and named authority",
      flowOutcome: "Organization contract active", flowOutcomeDetail: "future tasks remain bound by authority, capability, and context rules",
      loadedByConfig: "Safely loaded by startup configuration",
      exactVersionBound: "Exact version bound",
      materialPackName: "Current enterprise material pack",
      materialOrganizationName: "Current onboarded organization",
      componentComplete: "Complete",
      componentSource: "{component} material source",
      sourceRegistered: "{domain} source registered",
      authorityMatched: "{domain} authority owner matched",
      domainNames: { product: "Product", legal: "Legal", finance: "Finance", gtm: "GTM" },
      noSource: "The service has not returned an enterprise-input safe projection. This page will not fabricate one.",
      noValue: "Not supplied",
      source: "SOURCE", authority: "AUTHORITY", sensitivity: "SENSITIVITY", completeness: "COMPLETENESS", digest: "DIGEST",
      componentNames: { DOMAIN: "Domain", KNOWLEDGE: "Knowledge", AUTHORITY: "Authority", CAPABILITY: "Capability", DEPENDENCY: "Dependency" },
      slotNames: {
        product_plan: "Enterprise plan", launch_date: "Launch date", data_residency: "Data residency",
        notice_required: "Customer notice", price_band: "Price band", currency: "Quote currency",
        pricing_policy: "Pricing policy", quote_basket: "Quote line items",
        partner_terms: "Partner terms", quote_compose_skill: "Quote compose Skill", public_message: "Public message",
      },
      sensitivityNames: { PUBLIC: "Public", INTERNAL: "Internal", CONFIDENTIAL: "Confidential", RESTRICTED: "Restricted" },
      materialValueNames: {
        USD: "US dollars (USD)",
        "US and EU regions supported": "US and EU regions supported",
        "legal-review": "Legal review required",
        strategic: "Strategic-account pricing",
        "Evergreen Enterprise Plus": "Evergreen Enterprise Plus",
        "The enterprise launch remains on the approved schedule.": "The enterprise launch remains on the approved schedule.",
        "skill:enterprise-quote-compose@1.0": "Enterprise quote composition Skill",
      },
      materialPackNames: { "pack:evergreen-enterprise-quote@r1": "Evergreen Industries enterprise quote onboarding pack" },
      organizationNames: { "org:evergreen-industries": "Evergreen Industries" },
      customerNames: { "customer:blue-harbor": "Blue Harbor customer" },
      routeUnavailable: "Page unavailable",
      routeUnavailableBody: "The page component could not be loaded. Refresh and try again.",
      routeInvalid: "This link does not identify a page. You are back at enterprise onboarding; use the navigation to continue. Existing work is unchanged.",
      routeNext: "Next",
      stageNames: {
        EMPTY: "Awaiting employee request", CURRENT: "Current result", PREVIEWED: "Change awaiting approval", APPROVED: "Change approved · awaiting apply",
        RECOVERY_REQUIRED: "Change applied · result recovery required",
      },
      runPending: "Business run awaiting formation", runAwaitingEmployee: "Business run starts after employee request", runCandidatePending: "Business run starts after employee confirmation", runActive: "Business run in progress", runRecorded: "Result created; checking completion records", runComplete: "Current task completed",
      taskCandidateStage: "Task candidate awaiting confirmation",
      caseBadge: "CURRENT WORK ITEM",
      taskIntakeKicker: "NEW TASK · ENTERPRISE QUOTE",
      taskIntakeTitle: "Describe the request and let the team prepare the Quote",
      taskIntakeBody: "Customers, facts, and permissions come from the admitted organization contract. Confirm the scope to start the team. Subsequent rule changes require approval from the designated owner.",
      taskCompletedKicker: "EMPLOYEE INTAKE RECORD · BOUND TO THIS RUN",
      taskCompletedTitle: "Employee request confirmed",
      taskCompletedBody: "The work description was validated and the Agent team was started. Customer, facts, and authority remained constrained by the admitted organization contract.",
      taskUnverifiedKicker: "EXISTING QUOTE · INTAKE RECORD UNVERIFIED",
      taskUnverifiedTitle: "Quote exists; task-intake record unverified",
      taskPromptLabel: "BASELINE WORK DESCRIPTION (required, at least 8 characters)",
      taskPromptExample: "Create an enterprise quote for Blue Harbor that follows current Product, Legal, Finance, and GTM rules.",
      taskPrivateTitle: "EMPLOYEE-SUBMITTED WORK DESCRIPTION",
      taskPrivateScope: "Visible only to the current task actor · excluded from public evidence, events, and telemetry",
      taskPrivateLoading: "Loading the private work description for this task…",
      taskPrivateNotRetained: "The original work description was not retained for this run.",
      taskPrivateRestricted: "Only the task requester can view the original work description.",
      taskPrivateUnavailable: "The work description is unavailable. Try again later.",
      taskActorLabel: "REQUESTED BY",
      taskCustomerLabel: "CUSTOMER",
      taskKindLabel: "Task type",
      taskKindValue: "Enterprise Quote",
      taskContractLabel: "ORGANIZATION CONTRACT",
      taskContractReady: "Ready · server-verified at start",
      taskContractBlocked: "Enterprise onboarding is incomplete",
      taskCompleteOnboarding: "Complete enterprise onboarding first",
      taskPrepare: "Validate description and create candidate",
      taskPreparing: "Validating and creating constrained candidate…",
      taskAdmit: "Confirm scope and admit task",
      taskAdmitting: "Binding employee confirmation…",
      taskAdmittedButton: "Task admitted",
      taskRun: "Start Agent team",
      taskRunning: "Starting Agent team…",
      taskCandidateTitle: "CONSTRAINED TASK CANDIDATE",
      taskCandidateReady: "Awaiting employee confirmation",
      taskCandidateHold: "Held for human resolution",
      taskTemplateLabel: "SELECTED TEMPLATE",
      taskTemplateValue: "Enterprise Quote standard template",
      taskDigestLabel: "START EVIDENCE",
      taskChainProof: "Employee candidate confirmed · organization contract verified · Agent team started",
      taskCandidateProof: "Candidate created · awaiting employee confirmation",
      taskHoldProof: "Work-intent validation did not pass · Agent team not started",
      taskAdmittedProof: "Employee confirmation bound · waiting to start the Agent team",
      taskAdmittedRetryProof: "Employee confirmation bound · previous start incomplete, safe to retry",
      taskConstraintLabel: "CONTRACT CONSTRAINTS",
      taskStartPrerequisitesLabel: "START PREREQUISITES",
      taskHoldReasonLabel: "HOLD REASONS",
      taskNoUnknowns: "Organization contract and task scope verified · task may start",
      taskUnknownFallback: "The server returned an unresolved item that needs human review",
      taskInputProof: "Work-description digest bound · {length} characters",
      taskConfirm: "Confirm scope and admit task",
      taskAdmitted: "Employee confirmed · waiting for an explicit start",
      taskAdmittedRetry: "Employee confirmed · start incomplete, safe to retry",
      taskStarting: "Checking the organization contract and starting the task…",
      taskStarted: "Employee request confirmed; Agent team started",
      taskStartedBadge: "COLLABORATIVE WORKING DRAFT FORMED",
      taskEvidenceUnavailable: "TASK INTAKE EVIDENCE UNAVAILABLE",
      taskEvidenceUnavailableDetail: "A Quote exists, but no durable Task Intake receipt bound to the current run was observed. The UI does not infer employee confirmation.",
      taskOacBound: "OAC organization contract exactly bound",
      taskOacOptional: "This deployment does not require an OAC activation binding",
      taskBoundary: "A work description (Prompt) grants no authority, contributes no domain facts, and cannot bypass later Product or Finance approvals. This MVP validates only Enterprise Quote work.",
      taskError: "Request handling failed. Check the input or organization-contract state.",
      taskIdentityError: "The current operator does not match the task actor admitted by the organization profile.",
      taskContractError: "The OAC organization contract has not satisfied this task's start conditions.",
      taskReasonNames: {
        PROMPT_REQUIRED_OR_TOO_SHORT: "The work request must contain at least 8 characters",
        PROMPT_TASK_INTENT_MISMATCH: "The work description does not match the currently admitted Enterprise Quote task",
        TASK_ACTOR_OUT_OF_PROFILE_SCOPE: "The actor is outside the admitted organization profile",
        CUSTOMER_OUT_OF_PROFILE_SCOPE: "The customer is outside the admitted task scope",
        DELIVERABLE_KIND_OUT_OF_PROFILE_SCOPE: "The deliverable is not admitted by this template",
        PROMPT_AUTHORITY_OVERRIDE_REQUESTED: "The request attempts to bypass an approval or authority boundary",
        OAC_ADAPTATION_EXACT_ENTERPRISE_PACK_REQUIRED: "The exact OAC enterprise-input binding is incomplete",
        OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED: "The OAC activation binding is incomplete",
      },
      taskUnknownNames: {
        task_intent: "task intent",
        admitted_task_actor: "admitted task actor",
        admitted_customer: "admitted customer",
        admitted_deliverable_kind: "admitted deliverable",
        requested_authority: "requested authority",
        oac_activation_binding: "OAC activation binding",
      },
      skillCenterKicker: "CROSS-RUN CAPABILITY GOVERNANCE",
      skillCenterTitle: "Agents use versioned capabilities; experience is released only after human review",
      skillCenterBody: "This page does not make the current Quote decision. It separates exact invocations, released Skills, and experience candidates distilled from terminal runs so Agents cannot rewrite their own capabilities without governance.",
      skillCenterBadge: "Skill Governance",
      skillCurrentTitle: "CAPABILITY INVOCATION IN THIS TASK",
      skillCurrentBoundary: "Shown only after the Agent, same-run Skill activity row, and receipt match exactly",
      skillCurrentAgent: "CALLING AGENT",
      skillCurrentCapability: "CAPABILITY & VERSION",
      skillCurrentPurpose: "WHY IT WAS CALLED",
      skillCurrentEffect: "RESULT & AUTHORITY BOUNDARY",
      skillCurrentWaiting: "Awaiting a Skill invocation receipt from the current task",
      skillPublishedTitle: "RELEASED CAPABILITIES",
      skillPublishedBoundary: "DISCOVERABLE · EXACT VERSION · EVALUATED RELEASE",
      skillExperienceTitle: "EXPERIENCE CANDIDATE REVIEW",
      skillExperienceBoundary: "Run experience remains a candidate; this human decision approves a Skill release, not a business result",
      skillLifecycle: "Terminal run → Experience candidate → Independent evaluation → Human decision → Controlled CANARY dry-run",
      publicEvidence: "ADD-ON VALIDATION · BPI 2019",
      publicEvidenceDetail: "Real public process; not Quote data",
    }),
  });

  const byId = (id) => document.getElementById(id);
  const html = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
  const short = (value, width = 18) => {
    const text = String(value || "—");
    return text.length <= width ? text : `${text.slice(0, Math.max(8, width - 7))}…${text.slice(-6)}`;
  };
  const format = (template, values) => Object.entries(values || {}).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)), template,
  );

  let language = document.documentElement.lang === "en" ? "en" : "zh-CN";
  let currentRoute = "onboarding";
  const activeTabs = { quote: "work", onboarding: "materials", assurance: "skills" };
  let currentState = null;
  let stateAvailability = "loading";
  let activeLifecycle = "daily";
  let taskCandidate = null;
  let taskApproval = null;
  let taskIntakeBusy = false;
  let taskIntakeAction = "idle";
  let taskIntakeError = null;
  let taskWorkDescription = null;
  let taskWorkDescriptionRunId = null;
  let taskWorkDescriptionStatus = "idle";
  let taskWorkDescriptionContext = null;
  let taskWorkDescriptionGeneration = 0;
  let shell = null;
  let pages = new Map();
  let invalidRoute = false;

  function copy() { return COPY[language]; }
  function routeFromHash() {
    invalidRoute = false;
    const raw = String(window.location.hash || "").replace(/^#\/?/, "").split(/[?&]/)[0];
    const parts = raw.split("/");
    const route = ROUTE_ALIASES[parts[0]] || parts[0];
    if (!ROUTES.some((item) => item.id === route) || parts.length > 2) { invalidRoute = Boolean(raw); return "onboarding"; }
    let tab = SUBROUTE_TABS[parts[0]];
    if (parts.length === 2) {
      const requested = parts[1];
      const match = Object.keys(SUBROUTE_TABS).find((key) => (
        (ROUTE_ALIASES[key] || key) === route
        && (key === requested || SUBROUTE_TABS[key] === requested)
      ));
      if (!match) { invalidRoute = true; return "onboarding"; }
      tab = SUBROUTE_TABS[match];
    }
    if (tab && activeTabs[route] !== undefined) activeTabs[route] = tab;
    return route;
  }

  function routeDescriptor(route) {
    return ROUTES.find((item) => item.id === route) || ROUTES[0];
  }

  function make(tag, className, attributes = {}) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    Object.entries(attributes).forEach(([key, value]) => {
      if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    });
    return node;
  }

  function page(id) {
    const section = make("section", "shell-page", { "data-page": id, "aria-labelledby": "shell-page-title" });
    section.hidden = true;
    pages.set(id, section);
    return section;
  }

  function unavailable(route) {
    const card = make("article", "shell-unavailable");
    card.innerHTML = `<strong>${html(copy().routeUnavailable)}</strong><p>${html(copy().routeUnavailableBody)}</p>`;
    card.dataset.route = route;
    return card;
  }

  function buildTabbedPage(route, tabs, ariaCopyKey) {
    const section = page(route);
    const navigation = make("div", "shell-subnav", {
      role: "tablist",
      "data-shell-tablist": route,
      "data-shell-aria-label": ariaCopyKey,
    });
    const body = make("div", "shell-subpages");
    tabs.forEach(({ id, nodes }) => {
      const button = make("button", "shell-subnav-item", {
        type: "button",
        role: "tab",
        "data-shell-tab": id,
        "data-shell-tab-route": route,
        "aria-controls": `shell-${route}-${id}`,
      });
      button.textContent = copy().tabs[id];
      button.addEventListener("click", () => activateShellTab(route, id, { focus: true }));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const buttons = Array.from(navigation.querySelectorAll("[data-shell-tab]"));
        const index = buttons.indexOf(button);
        const nextIndex = event.key === "Home"
          ? 0
          : event.key === "End"
            ? buttons.length - 1
            : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
        event.preventDefault();
        activateShellTab(route, buttons[nextIndex].dataset.shellTab, { focus: true });
      });
      navigation.append(button);
      const panel = make("section", "shell-subpage", {
        id: `shell-${route}-${id}`,
        role: "tabpanel",
        "data-shell-panel": id,
        "data-shell-panel-route": route,
      });
      panel.hidden = true;
      nodes.filter(Boolean).forEach((node) => {
        if (node.getAttribute && node.getAttribute("role") === "tabpanel") {
          node.removeAttribute("role");
          node.removeAttribute("aria-labelledby");
        }
        node.hidden = false;
        panel.append(node);
      });
      body.append(panel);
    });
    section.append(navigation, body);
    return section;
  }

  function activateShellTab(route, tabId, { focus = false } = {}) {
    const pageNode = pages.get(route);
    if (!pageNode) return;
    const valid = Array.from(pageNode.querySelectorAll("[data-shell-tab]")).some(
      (node) => node.dataset.shellTab === tabId,
    );
    const next = valid ? tabId : pageNode.querySelector("[data-shell-tab]")?.dataset.shellTab;
    if (!next) return;
    activeTabs[route] = next;
    pageNode.querySelectorAll("[data-shell-tab]").forEach((button) => {
      const active = button.dataset.shellTab === next;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.tabIndex = active ? 0 : -1;
      if (focus && active) button.focus({ preventScroll: true });
    });
    pageNode.querySelectorAll("[data-shell-panel]").forEach((panel) => {
      const active = panel.dataset.shellPanel === next;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
    });
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function buildTaskCompass() {
    const section = make("section", "task-compass", { id: "task-compass", "aria-labelledby": "task-compass-title" });
    section.innerHTML = `
      <header><span data-shell-copy="compassKicker"></span><strong id="task-compass-title" data-shell-copy="compassTitle"></strong></header>
      <div>
        <article><span data-shell-copy="compassDemand"></span><strong id="task-compass-demand"></strong></article>
        <article><span data-shell-copy="compassTeam"></span><strong id="task-compass-team"></strong></article>
        <article><span data-shell-copy="compassCapability"></span><strong id="task-compass-capability"></strong></article>
        <article><span data-shell-copy="compassApproval"></span><strong id="task-compass-approval"></strong></article>
        <article><span data-shell-copy="compassDelivery"></span><strong id="task-compass-delivery"></strong></article>
      </div>`;
    return section;
  }

  function buildRunProgress() {
    const section = make("section", "observed-run-progress", {
      id: "observed-run-progress",
      "aria-labelledby": "observed-run-progress-title",
      "aria-live": "polite",
    });
    section.innerHTML = `
      <header>
        <div><span data-shell-copy="progressKicker"></span><strong id="observed-run-progress-title" data-shell-copy="progressTitle"></strong></div>
        <div><em id="observed-run-progress-status"></em><small class="mono" id="observed-run-progress-id">—</small><button class="button button-quiet" id="observed-run-progress-resume" type="button" data-shell-copy="progressResume" hidden></button></div>
      </header>
      <div class="observed-run-progress-grid" id="observed-run-progress-grid"></div>
      <p data-shell-copy="progressBoundary"></p>
      <details class="observed-run-details" id="observed-run-details" hidden>
        <summary id="observed-run-details-summary"></summary>
        <div id="observed-run-details-content"></div>
        <p data-shell-copy="progressUsageBoundary"></p>
      </details>
      <div class="element-observation">
        <span id="element-observation-status"></span>
        <button class="button button-quiet" id="element-observation-sync" type="button" data-shell-copy="elementSync" hidden></button>
        <a class="button button-quiet" id="element-observation-open" target="_blank" rel="noopener noreferrer" data-shell-copy="elementOpen" hidden></a>
        <small data-shell-copy="elementBoundary"></small>
      </div>`;
    section.querySelector("#observed-run-progress-resume").addEventListener("click", followRunProgress);
    section.querySelector("#element-observation-sync").addEventListener("click", syncElementObservation);
    return section;
  }

  function buildLifecycleRail() {
    const rail = make("ol", "shell-lifecycle-rail", {
      id: "shell-lifecycle-rail",
      "data-shell-aria-label": "lifecycleAria",
    });
    rail.innerHTML = `
      <li data-lifecycle-step="onboarding"><span>01</span><div><strong data-shell-copy="lifecycleOnboardingStep"></strong><small data-shell-copy="lifecycleOnboardingHint"></small></div><em></em></li>
      <li data-lifecycle-step="quote"><span>02</span><div><strong data-shell-copy="lifecycleQuoteStep"></strong><small data-shell-copy="lifecycleQuoteHint"></small></div><em></em></li>
      <li data-lifecycle-step="assurance"><span>03</span><div><strong data-shell-copy="lifecycleAssuranceStep"></strong><small data-shell-copy="lifecycleAssuranceHint"></small></div><em></em></li>`;
    return rail;
  }

  function buildTaskPrerequisite() {
    const banner = make("section", "task-prerequisite-banner", {
      id: "task-prerequisite-banner",
      "aria-live": "polite",
    });
    banner.innerHTML = `
      <div><span data-shell-copy="prerequisiteKicker"></span><strong id="task-prerequisite-title"></strong><small id="task-prerequisite-body"></small></div>
      <button class="button button-primary" id="task-prerequisite-action" type="button" data-route-target="oac" data-shell-copy="prerequisiteAction"></button>`;
    return banner;
  }

  function buildTaskExecutionPreview() {
    const section = make("section", "task-execution-preview", {
      id: "task-execution-preview",
      "aria-labelledby": "task-execution-preview-title",
    });
    section.innerHTML = `
      <header><span data-shell-copy="executionPreviewKicker"></span><strong id="task-execution-preview-title" data-shell-copy="executionPreviewTitle"></strong></header>
      <div>
        <article><b>01</b><strong data-shell-copy="executionPreviewAt"></strong><small data-shell-copy="executionPreviewAtBody"></small></article>
        <article><b>02</b><strong data-shell-copy="executionPreviewCapability"></strong><small data-shell-copy="executionPreviewCapabilityBody"></small></article>
        <article><b>03</b><strong data-shell-copy="executionPreviewAuthority"></strong><small data-shell-copy="executionPreviewAuthorityBody"></small></article>
        <article><b>04</b><strong data-shell-copy="executionPreviewFailure"></strong><small data-shell-copy="executionPreviewFailureBody"></small></article>
      </div>
      <p data-shell-copy="executionPreviewBoundary"></p>`;
    return section;
  }

  function buildTaskProcessBaseline(acceptance) {
    const section = make("section", "task-process-baseline", {
      id: "task-process-baseline",
      "aria-labelledby": "task-process-baseline-title",
    });
    section.innerHTML = `
      <header>
        <span data-shell-copy="processBaselineKicker"></span>
        <strong id="task-process-baseline-title" data-shell-copy="processBaselineTitle"></strong>
        <small data-shell-copy="processBaselineBoundary"></small>
      </header>`;
    if (!acceptance) return section;
    const directChildren = Array.from(acceptance.children);
    const valueLane = directChildren.find(
      (node) => node.classList.contains("lane-note") && node.classList.contains("value-lane"),
    );
    const processDetails = directChildren.find((node) => node.classList.contains("acceptance-support-details"));
    [valueLane, processDetails].filter(Boolean).forEach((node) => section.append(node));
    return section;
  }

  function buildOverview() {
    const section = page("overview");
    const dashboard = make("section", "overview-dashboard");
    dashboard.innerHTML = `
      <article><span data-shell-copy="overviewProblem"></span><strong data-shell-copy="overviewProblemValue"></strong></article>
      <article><span data-shell-copy="overviewSolution"></span><strong data-shell-copy="overviewSolutionValue"></strong></article>
      <article class="accent"><span data-shell-copy="overviewResult"></span><strong data-shell-copy="overviewResultValue"></strong></article>`;
    section.append(dashboard);
    const flow = make("section", "overview-flow-card");
    flow.innerHTML = `<div class="overview-flow" id="shell-overview-flow"></div><button type="button" class="shell-next" data-route-target="materials"></button>`;
    section.append(flow);
    const legacyHero = document.querySelector(".hero");
    if (legacyHero) {
      legacyHero.hidden = true;
      section.append(legacyHero);
    }
    const complexityBridge = document.querySelector(".complexity-bridge");
    if (complexityBridge) section.append(complexityBridge);
    return section;
  }

  function buildMaterials() {
    const section = page("materials");
    const intro = make("section", "materials-intro");
    intro.innerHTML = `
      <div><span data-shell-copy="materialsKicker"></span><h2 data-shell-copy="materialsTitle"></h2><p data-shell-copy="materialsBody"></p>
        <button type="button" class="shell-next" data-route-target="oac" data-shell-copy="openOac"></button></div>
      <span class="material-admission" id="shell-material-status"></span>`;
    section.append(intro);
    const facts = make("section", "material-summary", { id: "shell-material-summary", "aria-live": "polite" });
    facts.innerHTML = `<p>${html(copy().noSource)}</p>`;
    section.append(facts);
    const flow = make("section", "input-output-flow");
    flow.innerHTML = `
      <h3 data-shell-copy="inputToOutput"></h3>
      <div class="input-output-steps">
        <article><b>01</b><strong data-shell-copy="flowSource"></strong><small data-shell-copy="flowSourceDetail"></small></article><i>→</i>
        <article><b>02</b><strong data-shell-copy="flowOac"></strong><small data-shell-copy="flowOacDetail"></small></article><i>→</i>
        <article><b>03</b><strong data-shell-copy="flowContext"></strong><small data-shell-copy="flowContextDetail"></small></article><i>→</i>
        <article><b>04</b><strong data-shell-copy="flowOutcome"></strong><small data-shell-copy="flowOutcomeDetail"></small></article>
      </div>`;
    section.append(flow);
    return section;
  }

  function buildTaskIntake() {
    const section = make("section", "task-intake", { id: "task-intake", "aria-labelledby": "task-intake-title" });
    section.innerHTML = `
      <header class="task-intake-head">
        <div><span id="task-intake-kicker" data-shell-copy="taskIntakeKicker"></span><h2 id="task-intake-title" data-shell-copy="taskIntakeTitle"></h2><p id="task-intake-body" data-shell-copy="taskIntakeBody"></p></div>
        <b id="task-intake-contract"></b>
      </header>
      <div class="task-intake-form" id="task-intake-form">
        <label for="task-request-prompt"><span data-shell-copy="taskPromptLabel"></span><textarea id="task-request-prompt" rows="3" minlength="8" maxlength="500" required autocomplete="off" spellcheck="false"></textarea></label>
        <dl class="task-intake-scope">
          <div><dt data-shell-copy="taskActorLabel"></dt><dd id="task-intake-actor">—</dd></div>
          <div><dt data-shell-copy="taskCustomerLabel"></dt><dd id="task-intake-customer">—</dd></div>
          <div><dt data-shell-copy="taskKindLabel"></dt><dd data-shell-copy="taskKindValue"></dd></div>
        </dl>
        <button type="button" class="button button-primary" id="task-intake-prepare" data-shell-copy="taskPrepare"></button>
      </div>
      <section class="task-intake-private" id="task-intake-private" hidden aria-live="polite">
        <header><strong data-shell-copy="taskPrivateTitle"></strong><span data-shell-copy="taskPrivateScope"></span></header>
        <p id="task-intake-private-text"></p>
      </section>
      <section class="task-intake-candidate" id="task-intake-candidate" hidden aria-live="polite">
        <header><div><span data-shell-copy="taskCandidateTitle"></span><strong id="task-intake-candidate-status"></strong></div><b id="task-intake-candidate-badge"></b></header>
        <dl>
          <div><dt data-shell-copy="taskTemplateLabel"></dt><dd id="task-intake-template">—</dd></div>
          <div><dt data-shell-copy="taskDigestLabel"></dt><dd class="mono" id="task-intake-digest">—</dd></div>
          <div><dt id="task-intake-third-label" data-shell-copy="taskStartPrerequisitesLabel"></dt><dd id="task-intake-unknowns">—</dd></div>
        </dl>
        <div class="task-intake-actions">
          <button type="button" class="button button-secondary" id="task-intake-admit" data-shell-copy="taskAdmit"></button>
          <button type="button" class="button button-primary" id="task-intake-run" data-shell-copy="taskRun" hidden></button>
        </div>
      </section>
      <p class="task-intake-error" id="task-intake-error" role="alert" tabindex="-1" hidden></p>
      <p class="task-intake-boundary" data-shell-copy="taskBoundary"></p>`;
    return section;
  }

  function buildFromExisting(route, node, options = {}) {
    const section = page(route);
    if (node) {
      if (options.open && node.tagName === "DETAILS") node.open = true;
      if (node.getAttribute("role") === "tabpanel") {
        node.removeAttribute("role");
        node.removeAttribute("aria-labelledby");
      }
      node.hidden = false;
      section.append(node);
    } else section.append(unavailable(route));
    return section;
  }

  function buildShell() {
    const main = document.querySelector("main");
    if (!main || byId("enterprise-app-shell")) return;
    const originalTopbar = document.querySelector(".topbar");
    const oldCockpit = document.querySelector(".evidence-cockpit");
    const originalFooter = document.querySelector("body > footer");

    shell = make("div", "enterprise-app-shell", { id: "enterprise-app-shell" });
    const sidebar = make("aside", "shell-sidebar", {
      "aria-label": copy().workspaceNavigationAria,
      "data-shell-aria-label": "workspaceNavigationAria",
    });
    sidebar.innerHTML = `
      <div class="shell-brand"><span>知</span><div><strong>OrgRebase</strong><small data-shell-copy="product"></small></div></div>
      <div class="shell-case">
        <span id="shell-case-label" data-shell-copy="workspaceLabel"></span>
        <strong id="shell-case-name" data-shell-copy="workspaceName"></strong>
        <small id="shell-case-type" data-shell-copy="workspaceType"></small>
        <b id="shell-case-stage"></b>
      </div>
      <nav class="shell-nav" id="shell-navigation"></nav>
      <div class="shell-sidebar-evidence">
        <span data-shell-copy="publicEvidence"></span>
        <small data-shell-copy="publicEvidenceDetail"></small>
      </div>
      <div class="shell-sidebar-foot"><span class="shell-live-dot"></span><span data-shell-copy="environment"></span></div>`;
    const sessionPanel = byId("workspace-session");
    if (sessionPanel) sidebar.querySelector(".shell-case").after(sessionPanel);
    const nav = sidebar.querySelector("#shell-navigation");
    let previousSection = null;
    ROUTES.forEach((route) => {
      if (route.section !== previousSection) {
        const sectionKey = route.section === "primary" ? "sectionPrimary" : "sectionSecondary";
        const label = make("span", "shell-nav-section", {
          "data-shell-copy": sectionKey,
          "data-nav-section": route.section,
        });
        nav.append(label);
        previousSection = route.section;
      }
      const button = make("button", "shell-nav-item", {
        type: "button",
        "data-route": route.id,
        "data-nav-section": route.section,
      });
      button.innerHTML = `<span class="shell-nav-icon">${html(route.icon)}</span><span class="shell-nav-label"></span><small class="shell-nav-state"></small>`;
      button.addEventListener("click", () => navigate(route.id));
      nav.append(button);
    });

    const content = make("div", "shell-content");
    if (originalTopbar) content.append(originalTopbar);
    const pageHeader = make("header", "shell-page-header", { id: "shell-page-header" });
    pageHeader.innerHTML = `
      <div><span data-shell-copy="pageKicker"></span><h1 id="shell-page-title"></h1><p id="shell-page-summary"></p></div>
      <div class="shell-page-facts"><span><small id="shell-context-label" data-shell-copy="currentStage"></small><strong id="shell-stage"></strong></span><span><small id="shell-identity-label" data-shell-copy="currentRun"></small><strong id="shell-run"></strong></span></div>`;
    content.append(pageHeader);
    const routeNotice = make("p", "shell-unavailable", {
      id: "shell-route-notice", role: "status", "data-shell-copy": "routeInvalid",
    });
    routeNotice.hidden = true;
    content.append(routeNotice);
    const terminology = make("details", "task-context-details shell-terminology");
    terminology.innerHTML = `<summary><span data-shell-copy="termsTitle"></span><b aria-hidden="true">＋</b></summary>
      <dl class="shell-terminology-list">${["Oac", "Projection", "Authority", "Impact"].map((term) =>
        `<div><dt data-shell-copy="terms${term}Title"></dt><dd data-shell-copy="terms${term}"></dd></div>`).join("")}</dl>`;
    content.append(terminology);
    content.append(buildLifecycleRail());
    const pageRoot = make("div", "shell-pages", { id: "shell-pages" });
    content.append(pageRoot);
    shell.append(sidebar, content);
    document.body.insertBefore(shell, main);
    main.classList.add("shell-main");
    pageRoot.append(main);

    const acceptance = byId("current-task-acceptance");
    const taskWork = make("div", "task-work-flow");
    taskWork.append(
      buildTaskPrerequisite(),
      buildTaskExecutionPreview(),
      buildTaskProcessBaseline(acceptance),
      buildTaskCompass(),
      buildTaskIntake(),
    );
    [document.querySelector(".journey"), byId("command-bar"), byId("workspace")].forEach((node) => {
      if (node) taskWork.append(node);
    });
    const taskHero = document.querySelector("main > .hero");
    const taskComplexity = document.querySelector("main > .complexity-bridge");
    if (taskHero || taskComplexity) {
      const contextDetails = make("details", "task-context-details");
      contextDetails.innerHTML = `<summary><span data-shell-copy="taskContextSummary"></span><b aria-hidden="true">＋</b></summary>`;
      const contextBody = make("div", "task-context-details-body");
      if (taskHero) contextBody.append(taskHero);
      if (taskComplexity) contextBody.append(taskComplexity);
      contextDetails.append(contextBody);
      taskWork.append(contextDetails);
    }
    const collaboration = byId("cockpit-collaboration") || unavailable("collaboration");
    collaboration.prepend(buildRunProgress());
    const changes = make("div", "task-changes-flow", { id: "task-changes-flow" });
    const scene = byId("cockpit-scene");
    if (scene) changes.append(scene);
    if (acceptance) changes.append(acceptance);
    const businessBoard = byId("business-change-board");
    const technicalEvidence = byId("active-technical-evidence");
    if (businessBoard || technicalEvidence) {
      const details = make("details", "task-change-technical");
      details.innerHTML = `<summary><span>${html(language === "en" ? "Technical impact and authority receipts" : "技术影响与权威回执")}</span><b aria-hidden="true">＋</b></summary>`;
      if (businessBoard) details.append(businessBoard);
      if (technicalEvidence) details.append(technicalEvidence);
      changes.append(details);
    }
    const quote = buildTabbedPage("quote", [
      { id: "work", nodes: [taskWork] },
      { id: "collaboration", nodes: [collaboration] },
      { id: "changes", nodes: [changes] },
    ], "quoteTabsAria");

    const materials = buildMaterials();
    materials.classList.remove("shell-page");
    pages.delete("materials");
    const oac = byId("oac-adaptation") || unavailable("oac");
    if (oac.tagName === "DETAILS") oac.open = true;
    const onboarding = buildTabbedPage("onboarding", [
      { id: "materials", nodes: [materials] },
      { id: "oac", nodes: [oac] },
    ], "onboardingTabsAria");

    const validation = byId("cockpit-value") || unavailable("validation");
    const skills = byId("cockpit-skills") || unavailable("skills");
    const operations = byId("cockpit-operations") || unavailable("operations");
    const assurance = buildTabbedPage("assurance", [
      { id: "skills", nodes: [skills] },
      { id: "validation", nodes: [validation] },
      { id: "operations", nodes: [operations] },
    ], "assuranceTabsAria");
    [quote, onboarding, assurance].forEach((node) => main.append(node));

    const legacyHero = document.querySelector("main > .hero");
    const complexityBridge = document.querySelector("main > .complexity-bridge");
    if (legacyHero) legacyHero.hidden = true;
    if (complexityBridge) complexityBridge.hidden = true;

    if (oldCockpit) oldCockpit.hidden = true;
    const preflightMount = byId("oac-preflight-mount");
    if (preflightMount) preflightMount.remove();
    if (originalFooter) originalFooter.hidden = true;
    document.body.classList.add("shell-ready");
  }

  function positionPlatformGovernance() {
    const publishedSkills = byId("retained-skills");
    const publishedMount = byId("published-skills-mount");
    if (publishedSkills && publishedMount && publishedSkills.parentElement !== publishedMount) {
      publishedMount.append(publishedSkills);
    }
    const experience = byId("experience-governance");
    const experienceMount = byId("experience-governance-mount");
    if (experience && experienceMount && experience.parentElement !== experienceMount) {
      experienceMount.append(experience);
    }
    const retainedMechanism = byId("retained-mechanism");
    const validationView = byId("cockpit-value");
    const publicValidation = byId("public-real-process-validation");
    const currentProof = byId("archive-revalidation");
    if (
      publicValidation
      && validationView
      && currentProof
      && publicValidation.nextElementSibling !== currentProof
    ) {
      validationView.insertBefore(publicValidation, currentProof);
    }
    if (retainedMechanism && validationView && retainedMechanism.parentElement !== validationView) {
      validationView.append(retainedMechanism);
    }
  }

  function positionCollaborationContext() {
    const contextModel = byId("oac-context-model");
    const contextMount = byId("agent-context-model-mount");
    if (contextModel && contextMount && contextModel.parentElement !== contextMount) {
      contextMount.append(contextModel);
      contextModel.hidden = false;
      contextModel.removeAttribute("aria-hidden");
    }
  }

  function organizationContractGate(state = currentState) {
    const gate = state && state.workspace_gate;
    const runId = state && state.execution && state.execution.run_id;
    if (!gate || gate.schema_version !== "orgrebase.workspace-oac-gate.v1"
      || !runId || gate.execution_run_id !== runId
      || !["off", "optional", "required"].includes(gate.mode)
      || typeof gate.form_allowed !== "boolean"
      || typeof gate.requires_oac_admission !== "boolean") return null;
    if (gate.mode === "required") {
      if (gate.requires_oac_admission !== true) return null;
      if (gate.status === "BLOCKED_PENDING_OAC" && gate.form_allowed === false) return gate;
      if (!gate.activation_binding_digest) return null;
      if (gate.status === "READY_TO_FORM" && gate.form_allowed) return gate;
      return gate.status === "CONSUMED_BY_QUOTE_FORMATION" && gate.consumption_receipt_digest ? gate : null;
    }
    return gate.requires_oac_admission === false && gate.form_allowed === true
      && ["READY_TO_FORM", "CONSUMED_BY_QUOTE_FORMATION"].includes(gate.status) ? gate : null;
  }

  function organizationContractReady(state = currentState) {
    const gate = organizationContractGate(state);
    return Boolean(gate && gate.form_allowed);
  }

  function organizationContractEstablished(state = currentState) {
    const gate = organizationContractGate(state);
    return Boolean(gate && (gate.form_allowed || gate.status === "CONSUMED_BY_QUOTE_FORMATION"));
  }

  function onboardingContext(state = currentState) {
    const selected = copy();
    const gate = organizationContractGate(state);
    const status = gate ? gate.status : "UNAVAILABLE";
    const digest = gate && gate.activation_binding_digest || null;
    const optional = Boolean(gate && !gate.requires_oac_admission);
    const label = status === "CONSUMED_BY_QUOTE_FORMATION"
      ? selected.onboardingConsumed
      : optional
        ? selected.onboardingOptional
        : organizationContractReady(state)
          ? selected.onboardingReady
          : gate ? selected.onboardingPending : selected.workspaceUnavailable;
    return { status, label, digest, optional };
  }

  function setExactText(node, value, exact = null) {
    if (!node) return;
    node.textContent = value;
    if (exact == null || String(exact).trim() === "") node.removeAttribute("title");
    else node.setAttribute("title", String(exact));
  }

  function renderContextFacts(state = currentState) {
    const selected = copy();
    const contextLabel = byId("shell-context-label");
    const identityLabel = byId("shell-identity-label");
    const contextValue = byId("shell-stage");
    const identityValue = byId("shell-run");
    const caseLabel = byId("shell-case-label");
    const caseName = byId("shell-case-name");
    const caseType = byId("shell-case-type");
    const caseStage = byId("shell-case-stage");
    const scenario = state && state.scenario || {};

    if (currentRoute === "onboarding") {
      contextLabel.textContent = selected.onboardingContext;
      identityLabel.textContent = selected.onboardingIdentity;
      caseLabel.textContent = selected.currentOrganization;
      caseName.textContent = selected.organizationNames[String(scenario.organization_id || "")]
        || scenario.organization_name
        || selected.materialOrganizationName;
      caseType.textContent = selected.onboardingWorkspaceType;
    } else {
      const assuranceFacts = selected.dailyAssuranceFacts[currentRoute];
      const platformFacts = null;
      contextLabel.textContent = platformFacts
        ? selected.platformValidationContext
        : assuranceFacts
          ? selected.dailyAssuranceContext
          : selected.currentStage;
      identityLabel.textContent = platformFacts
        ? selected.platformValidationIdentity
        : assuranceFacts
          ? selected.dailyAssuranceIdentity
          : selected.currentRun;
      caseLabel.textContent = selected.workspaceLabel;
      const customerName = selected.customerNames[String(scenario.customer_id || "")];
      caseName.textContent = customerName
        ? `${customerName} · ${selected.taskKindValue}`
        : scenario.label || selected.workspaceName;
      caseType.textContent = platformFacts
        ? selected.platformValidationWorkspaceType
        : assuranceFacts
          ? selected.dailyAssuranceWorkspaceType
          : selected.workspaceType;
    }

    if (stateAvailability !== "ready" || !state || !STAGES.includes(state.stage)) {
      const unavailable = stateAvailability === "unavailable";
      const status = unavailable ? selected.workspaceUnavailable : selected.workspaceLoading;
      setExactText(contextValue, status);
      setExactText(identityValue, status);
      setExactText(caseStage, status);
      return;
    }

    if (currentRoute === "onboarding") {
      const onboarding = onboardingContext(state);
      setExactText(contextValue, onboarding.label, onboarding.status);
      setExactText(
        identityValue,
        onboarding.digest ? selected.onboardingIdentityBound
          : onboarding.optional ? selected.onboardingIdentityOptional : selected.onboardingIdentityPending,
        onboarding.digest,
      );
      setExactText(caseStage, onboarding.label, onboarding.status);
      return;
    }
    const assuranceFacts = selected.dailyAssuranceFacts[currentRoute];
    const platformFacts = null;
    const contextualFacts = platformFacts || assuranceFacts;
    if (contextualFacts) {
      setExactText(contextValue, contextualFacts[0]);
      setExactText(identityValue, contextualFacts[1]);
      setExactText(caseStage, selected.pages[currentRoute][0]);
      return;
    }

    const runId = state.execution && state.execution.run_id;
    const candidateReady = state.stage === "EMPTY"
      && taskCandidate
      && taskCandidate.status === "READY_FOR_CONFIRMATION";
    const visibleStage = candidateReady ? selected.taskCandidateStage : stageName(state.stage);
    setExactText(contextValue, visibleStage, candidateReady ? taskCandidate.digest : state.stage);
    setExactText(
      identityValue,
      candidateReady
        ? selected.runCandidatePending
        : runId
        ? state.stage === "EMPTY"
          ? selected.runAwaitingEmployee
          : runSummary(state)
        : selected.runPending,
      candidateReady ? taskCandidate.digest : runId,
    );
    setExactText(caseStage, visibleStage, candidateReady ? taskCandidate.digest : state.stage);
  }

  function renderOverviewFlow(state = currentState) {
    const selected = copy();
    const materialsNext = document.querySelector('.shell-page[data-page="onboarding"] [data-route-target="oac"]');
    if (materialsNext) {
      materialsNext.textContent = onboardingContext(state).digest ? selected.viewActiveOac : selected.openOac;
    }
    renderTaskCompass(state);
  }

  function renderLifecycle() {
    if (!shell) return;
    shell.dataset.route = currentRoute;
  }

  function renderLifecycleRail(state = currentState) {
    const selected = copy();
    const contractReady = organizationContractEstablished(state);
    document.querySelectorAll("#shell-lifecycle-rail [data-lifecycle-step]").forEach((item) => {
      const step = item.dataset.lifecycleStep;
      let status = "waiting";
      let label = selected.lifecycleLocked;
      if (step === "onboarding") {
        status = contractReady ? "done" : currentRoute === "onboarding" ? "current" : "waiting";
        label = contractReady ? selected.lifecycleDone : currentRoute === "onboarding" ? selected.lifecycleCurrent : selected.lifecycleLocked;
      } else if (step === "quote") {
        status = !contractReady ? "locked" : Boolean(state && state.business_complete) ? "done" : currentRoute === "quote" ? "current" : "ready";
        label = !contractReady ? selected.lifecycleLocked : Boolean(state && state.business_complete) ? selected.lifecycleDone : currentRoute === "quote" ? selected.lifecycleCurrent : selected.navState.ready;
      } else {
        status = currentRoute === "assurance" ? "current" : "audit";
        label = currentRoute === "assurance" ? selected.lifecycleCurrent : selected.lifecycleAudit;
      }
      item.dataset.state = status;
      const marker = item.querySelector("em");
      if (marker) marker.textContent = label;
    });
  }

  function renderTaskPrerequisite(state = currentState) {
    const selected = copy();
    const banner = byId("task-prerequisite-banner");
    const title = byId("task-prerequisite-title");
    const body = byId("task-prerequisite-body");
    const action = byId("task-prerequisite-action");
    if (!banner || !title || !body || !action) return;
    const gate = stateAvailability === "ready" ? organizationContractGate(state) : null;
    const ready = Boolean(gate && organizationContractEstablished(state));
    const blocked = Boolean(gate && gate.requires_oac_admission && !ready);
    banner.dataset.state = ready ? "ready" : blocked ? "blocked" : "checking";
    const inUse = ready && state && state.stage !== "EMPTY" && typeof state.quote?.version === "string";
    title.textContent = inUse ? gate.requires_oac_admission ? selected.prerequisiteInUseTitle : selected.prerequisiteBoundTitle : ready ? selected.prerequisiteReadyTitle
      : blocked ? selected.prerequisiteBlockedTitle : selected.prerequisiteCheckingTitle;
    body.textContent = inUse ? gate.requires_oac_admission ? selected.prerequisiteInUseBody : selected.prerequisiteOptionalBody : ready
      ? gate.requires_oac_admission ? selected.prerequisiteReadyBody : selected.prerequisiteOptionalBody
      : blocked ? selected.prerequisiteBlockedBody : selected.prerequisiteCheckingBody;
    action.hidden = !blocked;
    const executionPreview = byId("task-execution-preview");
    if (executionPreview) executionPreview.hidden = Boolean(state && state.stage !== "EMPTY");
  }

  function setCopy() {
    const selected = copy();
    document.querySelectorAll("[data-shell-copy]").forEach((node) => {
      const key = node.dataset.shellCopy;
      const value = selected[key];
      if (typeof value === "string") node.textContent = value;
    });
    document.querySelectorAll("[data-shell-aria-label]").forEach((node) => {
      const key = node.dataset.shellAriaLabel;
      const value = selected[key];
      if (typeof value === "string") node.setAttribute("aria-label", value);
    });
    ROUTES.forEach((route) => {
      const button = document.querySelector(`.shell-nav-item[data-route="${route.id}"]`);
      if (!button) return;
      button.querySelector(".shell-nav-label").textContent = selected.pages[route.id][0];
    });
    document.querySelectorAll("[data-shell-tab]").forEach((button) => {
      const label = selected.tabs[button.dataset.shellTab];
      if (label) button.textContent = label;
    });
    renderOverviewFlow(currentState);
    renderLifecycle();
    renderLifecycleRail(currentState);
    document.querySelectorAll("[data-route-target]").forEach((button) => {
      if (button.dataset.bound) return;
      button.dataset.bound = "true";
      button.addEventListener("click", () => navigate(button.dataset.routeTarget));
    });
    renderRouteHeader();
    renderState(currentState);
  }

  function stageIndex(stage) {
    const index = STAGES.indexOf(stage);
    return index < 0 ? 0 : index;
  }

  function stageName(stage) {
    return copy().stageNames[stage] || String(stage || copy().stageNames.EMPTY);
  }

  function runSummary(state) {
    if (!state || state.stage === "EMPTY") return copy().runPending;
    if (typeof currentRunValueProjection === "function" && currentRunValueProjection(state).status === "PASS") return copy().runComplete;
    if (typeof currentRunValueProjection === "function" && currentRunValueProjection(state).status === "BASELINE_VERIFIED") return copy().runBaseline;
    if (state.business_complete === true) return copy().runRecorded;
    return copy().runActive;
  }

  function taskApprovalCount(state) {
    return Object.keys(state && state.changes || {})
      .map((kind) => taskChangeProjection(state, kind))
      .filter((projection) => projection && projection.approved).length;
  }

  function taskChangeProjection(state, kind) {
    const change = state && state.changes && state.changes[kind];
    const previewEnvelope = change && change.preview;
    const bundle = previewEnvelope && (previewEnvelope.bundle || previewEnvelope);
    const changeSet = bundle && bundle.change_set;
    const changeSpec = bundle && bundle.change_spec;
    const delta = changeSet && Array.isArray(changeSet.deltas) ? changeSet.deltas[0] : null;
    if (!delta) return null;
    const advisory = bundle && bundle.advisory || {};
    const agentRuns = Array.isArray(advisory.agent_runs) ? advisory.agent_runs : [];
    const handoffs = Array.isArray(advisory.handoffs) ? advisory.handoffs : [];
    const orchestrationPlan = advisory.orchestration_plan || {};
    const planTasks = Array.isArray(orchestrationPlan.tasks) ? orchestrationPlan.tasks : [];
    const compilationReceipt = advisory.compilation_receipt || {};
    const coordinationReceipt = advisory.coordination_receipt || {};
    const ingestionReceipt = advisory.ingestion_receipt || {};
    const ingestionDecisions = Array.isArray(ingestionReceipt.decisions) ? ingestionReceipt.decisions : [];
    const runEnvelope = bundle && bundle.run_envelope || {};
    const executionRunId = state && state.execution && state.execution.run_id;
    const previewDigest = previewEnvelope && previewEnvelope.preview_digest;
    const runNonce = runEnvelope.nonce;
    const sourceValues = state.enterprise_data_lineage?.source?.values;
    const sourceDomains = new Set((Array.isArray(sourceValues) ? sourceValues : [])
      .filter(item => typeof item.object_ref === "string" && item.object_ref.startsWith(`${delta.object_id}@`))
      .map(item => item.domain_id));
    const sourceDomain = sourceDomains.size === 1 ? [...sourceDomains][0] : null;
    const expectedAgents = sourceDomain === "finance" || delta.object_id === "policy:finance.currency"
      ? ["change-coordinator", "finance-steward", "gtm-steward"]
      : sourceDomain && sourceDomain !== "product" ? []
        : ["change-coordinator", "product-steward", "gtm-steward"];
    const expectedOwner = changeSpec.owner_id;
    const expectedObject = changeSpec.object_id;
    const agents = agentRuns.map((run) => run && run.agent_name).filter(Boolean);
    const agentRunDigests = agentRuns.map((run) => run && run.digest).filter(Boolean);
    const handoffDigests = handoffs.map((handoff) => handoff && handoff.digest).filter(Boolean);
    const admittedCandidateDigests = Array.isArray(ingestionReceipt.admitted_candidate_digests)
      ? ingestionReceipt.admitted_candidate_digests
      : [];
    const exactAgentBindings = agentRuns.every((run, index) => {
      const task = planTasks[index] || {};
      const handoff = handoffs[index] || {};
      const payload = handoff.payload || {};
      const changeObjectIds = Object.hasOwn(payload, "change_object_ids")
        ? payload.change_object_ids : [payload.change_object_id];
      const decision = ingestionDecisions[index] || {};
      return Boolean(
        run
        && task.id === run.task_id
        && task.agent_name === run.agent_name
        && task.candidate_only === true
        && task.digest === handoff.delegation_task_digest
        && run.workflow_run_id === executionRunId
        && run.run_nonce === runNonce
        && run.status === "TRUSTED_COMPLETE"
        && handoff.task_id === run.task_id
        && handoff.from_agent === run.agent_name
        && handoff.to_agent === "orgrebase-control-plane"
        && handoff.workflow_run_id === executionRunId
        && handoff.run_nonce === runNonce
        && handoff.candidate_only === true
        && handoff.orchestration_plan_digest === orchestrationPlan.digest
        && payload.candidate_only === true
        && payload.preview_digest === previewDigest
        && JSON.stringify(changeObjectIds) === JSON.stringify([changeSpec.object_id])
        && payload.target_writes === 0
        && Array.isArray(task.allowed_output_kinds)
        && task.allowed_output_kinds.includes(payload.kind)
        && decision.producer_worker === run.agent_name
        && decision.artifact_ref === handoff.id
        && decision.artifact_digest === handoff.digest
        && decision.decision === "ADVISORY_ACCEPTED"
        && Array.isArray(decision.admitted_effects)
        && decision.admitted_effects.length === 0
      );
    });
    const exactBindings = Boolean(
      executionRunId
      && previewDigest
      && runNonce
      && changeSpec && changeSpec.owner_id === expectedOwner
      && changeSpec.object_id === expectedObject
      && changeSet && changeSet.owner_id === changeSpec.owner_id
      && changeSet.id === changeSpec.id
      && runEnvelope.run_id === executionRunId
      && JSON.stringify(agents) === JSON.stringify(expectedAgents)
      && JSON.stringify(planTasks.map((task) => task && task.agent_name)) === JSON.stringify(expectedAgents)
      && agentRuns.length === expectedAgents.length
      && handoffs.length === expectedAgents.length
      && ingestionDecisions.length === expectedAgents.length
      && exactAgentBindings
      && orchestrationPlan.preview_digest === previewDigest
      && orchestrationPlan.change_set_digest === changeSet.digest
      && compilationReceipt.preview_digest === previewDigest
      && compilationReceipt.change_set_digest === changeSet.digest
      && compilationReceipt.orchestration_plan_digest === orchestrationPlan.digest
      && coordinationReceipt.workflow_run_id === executionRunId
      && coordinationReceipt.run_nonce === runNonce
      && coordinationReceipt.orchestration_plan_digest === orchestrationPlan.digest
      && JSON.stringify(coordinationReceipt.agent_run_digests) === JSON.stringify(agentRunDigests)
      && JSON.stringify(coordinationReceipt.handoff_digests) === JSON.stringify(handoffDigests)
      && coordinationReceipt.status === "PASS"
      && ingestionReceipt.run_id === executionRunId
      && ingestionReceipt.nonce === runNonce
      && ingestionReceipt.preview_digest === previewDigest
      && ingestionReceipt.change_set_digest === changeSet.digest
      && ingestionReceipt.orchestration_plan_digest === orchestrationPlan.digest
      && ingestionReceipt.live_receipt_digest === coordinationReceipt.digest
      && ingestionReceipt.target_writes === 0
      && JSON.stringify(admittedCandidateDigests) === JSON.stringify(handoffDigests)
      && Array.isArray(ingestionReceipt.rejected_candidate_digests)
      && ingestionReceipt.rejected_candidate_digests.length === 0
    );
    if (!exactBindings) return null;
    const approvalEnvelope = change && change.approval;
    const approval = approvalEnvelope && (approvalEnvelope.approval || approvalEnvelope);
    const approvalDigest = approvalEnvelope && (approvalEnvelope.approval_digest || approvalEnvelope.artifact_digest);
    const approved = Boolean(
      approvalDigest
      && approval
      && approval.digest === approvalDigest
      && approval.actor_id === changeSpec.owner_id
      && approval.preview_digest === previewDigest
    );
    return {
      kind,
      slotId: (state.change_events || []).find(event => event.event_id === kind && event.object_id === delta.object_id)?.slot_id || kind,
      delta,
      agents,
      owner: changeSpec.owner_id,
      skills: [...new Set(agentRuns.flatMap((run) => Array.isArray(run && run.skill_versions) ? run.skill_versions : []))],
      tools: [...new Set(agentRuns.flatMap((run) => Array.isArray(run && run.tool_versions) ? run.tool_versions : []))],
      approved,
    };
  }

  function taskCurrentChange(state) {
    const latest = state && state.latest_preview;
    const latestKind = latest && Object.keys(state && state.changes || {}).includes(latest.kind) ? latest.kind : null;
    if (latestKind) {
      const stored = state.changes && state.changes[latestKind] && state.changes[latestKind].preview;
      if (!stored || latest.preview_digest !== stored.preview_digest) return null;
      return taskChangeProjection(state, latestKind);
    }
    return Object.keys(state && state.changes || {}).reverse().map((eventId) => taskChangeProjection(state, eventId)).find(Boolean) || null;
  }

  function taskChangeLabel(selected, kind) {
    return kind === "currency" ? selected.compassChangeCurrency : kind === "launch_date" ? selected.compassChangeLaunch
      : selected.slotNames?.[kind] || kind;
  }

  function taskChangeTeam(selected, change) {
    if (!change.agents.length) return selected.compassTeamUnavailable;
    return change.agents.map((actor) => selected.compassActors[actor] || actor).join(" + ");
  }

  function taskOwnerLabel(selected, change) {
    if (change.owner === "human:evergreen-product-owner") return selected.compassOwnerProduct;
    if (change.owner === "human:evergreen-finance-owner") return selected.compassOwnerFinance;
    return change.owner;
  }

  function taskChangeCapabilities(selected, change) {
    const skills = change.skills.length ? change.skills.join(" + ") : selected.compassNoSkill;
    const externalTools = change.tools.filter((tool) => !String(tool).includes("none-proposal-only"));
    const tools = externalTools.length ? externalTools.join(" + ") : selected.compassNoExternalTool;
    return { skills, tools };
  }

  function renderTaskCompass(state = currentState) {
    const selected = copy();
    const prompt = byId("task-request-prompt");
    const currentChange = taskCurrentChange(state);
    const changeLabel = currentChange && taskChangeLabel(selected, currentChange.slotId);
    const demand = currentChange
      ? format(selected.compassDemandCurrent, {
        change: changeLabel,
        from: typeof structuredBusinessValue === "function" ? structuredBusinessValue(currentChange.delta.base_value, currentChange.slotId, language) ?? currentChange.delta.base_value : currentChange.delta.base_value,
        to: typeof structuredBusinessValue === "function" ? structuredBusinessValue(currentChange.delta.proposed_value, currentChange.slotId, language) ?? currentChange.delta.proposed_value : currentChange.delta.proposed_value,
      })
      : taskWorkDescriptionStatus === "available" && taskWorkDescription
        ? taskWorkDescription
        : prompt && prompt.value.trim()
          ? prompt.value.trim()
          : selected.compassDemandWaiting;
    setExactText(byId("task-compass-demand"), demand);
    setExactText(
      byId("task-compass-team"),
      currentChange
        ? format(selected.compassTeamCurrentValue, { change: changeLabel, team: taskChangeTeam(selected, currentChange) })
        : selected.compassTeamBaselineValue,
    );
    const capabilities = currentChange && taskChangeCapabilities(selected, currentChange);
    setExactText(
      byId("task-compass-capability"),
      currentChange
        ? format(selected.compassCapabilityCurrentValue, capabilities)
        : selected.compassCapabilityBaselineValue,
    );
    const verifiedChanges = Object.keys(state?.changes || {}).map(kind => taskChangeProjection(state, kind)).filter(change => change?.approved);
    const owners = [...new Set(verifiedChanges.map(change => taskOwnerLabel(selected, change)))];
    const noChanges = state?.change_history?.total === 0 && Array.isArray(state.change_events)
      && state.change_events.length === 0 && Object.keys(state.changes || {}).length === 0;
    setExactText(byId("task-compass-approval"), noChanges ? selected.compassApprovalNone
      : currentChange ? format(selected.compassApprovalCurrent, {owner: taskOwnerLabel(selected, currentChange), count: currentChange.approved ? 1 : 0})
      : verifiedChanges.length ? format(selected.compassApprovalComplete, {count: verifiedChanges.length, owners: owners.join(" + ")})
      : selected.compassApprovalWaiting);
    const quote = state && state.quote || {};
    const payload = quote.payload || {};
    const delivery = quote.version
      ? [quote.version, payload.launch_date, payload.currency].filter(Boolean).join(" · ")
      : selected.compassDeliveryWaiting;
    setExactText(byId("task-compass-delivery"), delivery);
  }

  let runProgressTimer = null;
  let elementObservationBusy = false;
  let elementObservationError = null;

  function elementObservationFailure(code, fallback) {
    const selected = copy();
    // Classify only known codes. Never display a remote response or exception message.
    if (typeof code !== "string") return fallback;
    let key;
    if (["MATRIX_OBSERVATION_NOT_CONFIGURED", "MATRIX_OBSERVATION_CONFIG_INVALID",
      "MATRIX_OBSERVATION_URL_INVALID", "MATRIX_OBSERVATION_WORKSPACE_URL_REQUIRED",
      "MATRIX_OBSERVATION_WORKSPACE_SCOPE_INVALID", "MATRIX_OBSERVATION_TOKEN_MISSING",
      "MATRIX_OBSERVATION_IDENTITY_INVALID"].includes(code)) key = "elementConfigFailed";
    else if (code === "MATRIX_OBSERVATION_CONFIG_NOT_PRIVATE") key = "elementConfigPrivate";
    else if (["AUTH_SESSION_REQUIRED", "AUTH_BEARER_REQUIRED", "AUTH_TOKEN_INVALID",
      "AUTH_TOKEN_EXPIRED", "HTTP_401"].includes(code)) key = "elementSignInRequired";
    else if (["AUTH_ACTION_DENIED", "AUTH_MEMBERSHIP_DENIED", "AUTH_TENANT_DENIED",
      "MATRIX_OBSERVATION_TASK_ACTOR_REQUIRED", "HTTP_403"].includes(code)) key = "elementPermissionDenied";
    else if (["MATRIX_OBSERVATION_HTTP_401", "MATRIX_OBSERVATION_SENDER_MISMATCH",
      "MATRIX_OBSERVATION_PUBLISHER_IS_GUEST"].includes(code)) key = "elementPublisherCredentials";
    else if (["MATRIX_OBSERVATION_HTTP_403", "MATRIX_OBSERVATION_PUBLISHER_NOT_JOINED",
      "MATRIX_OBSERVATION_VIEWER_NOT_INVITED", "MATRIX_OBSERVATION_ROOM_PERMISSIONS_INVALID"].includes(code)) key = "elementRoomPermissions";
    else if (code === "MATRIX_OBSERVATION_WORKSPACE_FORBIDDEN") key = "elementWorkspaceDenied";
    else if (code === "MATRIX_OBSERVATION_NETWORK_FAILED"
      || /^(?:MATRIX_OBSERVATION_)?HTTP_(?:408|429|5\d{2})$/.test(code)) key = "elementConnectionFailed";
    return selected[key] || fallback;
  }

  function renderElementObservation(state) {
    const status = byId("element-observation-status");
    const button = byId("element-observation-sync");
    const link = byId("element-observation-open");
    if (!status || !button || !link) return;
    const selected = copy();
    const operations = state && state.agentteams_operations || {};
    const observation = operations.element_observation || { status: "NOT_CONFIGURED" };
    const run = operations.formation_taskflow || {};
    if (elementObservationError && (elementObservationError.workspaceId !== observation.workspace_id
      || elementObservationError.runId !== run.run_id)) elementObservationError = null;
    button.hidden = ["NOT_CONFIGURED", "WAITING_FOR_RUN"].includes(observation.status) || !run.run_id;
    button.disabled = elementObservationBusy;
    link.hidden = true;
    link.removeAttribute("href");
    status.textContent = elementObservationBusy ? selected.elementPublishing
      : observation.status === "NOT_CONFIGURED" ? selected.elementUnavailable
        : observation.status === "WAITING_FOR_RUN" ? selected.elementWaiting
          : observation.status === "UNAVAILABLE"
            ? elementObservationFailure(elementObservationError?.code ?? observation.error_code, selected.elementFailed)
            : observation.status === "STALE" ? selected.elementStale : selected.elementReady;
    if (observation.status === "OBSERVED" && observation.run_id === run.run_id) {
      try {
        const url = new URL(observation.element_room_url);
        if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return;
        link.href = url.href;
        link.hidden = false;
        if (!elementObservationBusy) status.textContent = format(selected.elementObserved, {
          revision: observation.snapshot.quote_revision ?? "—",
          time: new Date(observation.observed_at).toLocaleString(language === "en" ? "en-US" : "zh-CN"),
        });
      } catch (_) { status.textContent = selected.elementFailed; }
    }
    if (elementObservationError && !elementObservationBusy && observation.status !== "UNAVAILABLE") {
      status.textContent += " · " + elementObservationFailure(elementObservationError.code, selected.elementRequestFailed);
    }
  }

  async function syncElementObservation() {
    if (elementObservationBusy) return;
    const runId = currentState && currentState.agentteams_operations?.formation_taskflow?.run_id;
    const workspaceId = currentState?.agentteams_operations?.element_observation?.workspace_id;
    const actorId = taskScope(currentState).actorId;
    if (!runId || !workspaceId || !actorId) return;
    const isCurrentRun = () => currentState?.agentteams_operations?.formation_taskflow?.run_id === runId
      && currentState?.agentteams_operations?.element_observation?.workspace_id === workspaceId;
    elementObservationError = null;
    elementObservationBusy = true;
    renderElementObservation(currentState);
    try {
      await taskIntakeApi("/api/workspace/agentteams-observation", {
        actor_id: actorId, run_id: runId,
      }, actorId);
    } catch (error) {
      // A rejected request can leave the last verified observation unchanged.
      if (isCurrentRun()) elementObservationError = { workspaceId, runId, code: error?.code ?? error?.error_code };
    } finally {
      elementObservationBusy = false;
      if (isCurrentRun()) window.dispatchEvent(new CustomEvent("orgrebase:workspacechange"));
      renderElementObservation(currentState);
    }
  }
  let runProgressFollowing = false;
  let runProgressFollowDeadline = 0;
  let runProgressRetryCount = 0;
  let runProgressDetailsKey = null;
  const RUN_PROGRESS_FOLLOW_WINDOW_MS = 15 * 60 * 1000;
  const RUN_PROGRESS_POLL_INTERVAL_MS = 700;
  const RUN_PROGRESS_RETRY_DELAYS_MS = Object.freeze([1000, 2000, 4000]);

  function clearRunProgressTimer() {
    if (runProgressTimer !== null) window.clearTimeout(runProgressTimer);
    runProgressTimer = null;
  }

  function setRunProgressNotice(copyKey, values = {}, { canResume = false } = {}) {
    const selected = copy();
    const status = byId("observed-run-progress-status");
    const resume = byId("observed-run-progress-resume");
    if (status && selected[copyKey]) status.textContent = format(selected[copyKey], values);
    if (resume) resume.hidden = !canResume;
  }

  function renderRunProgress(progress) {
    const selected = copy();
    const root = byId("observed-run-progress");
    const grid = byId("observed-run-progress-grid");
    const status = byId("observed-run-progress-status");
    const identity = byId("observed-run-progress-id");
    const resume = byId("observed-run-progress-resume");
    if (!root || !grid || !status || !identity) return false;
    const expectedRun = currentState?.stage !== "EMPTY" && currentState?.execution?.run_id;
    const valid = stateAvailability !== "unavailable" && progress
      && progress.schema_version === "orgrebase.workspace-run-progress-view.v1"
      && ["WAITING", "RUNNING", "ACTIVE", "COMPLETED", "FAILED"].includes(progress.status)
      && (!expectedRun || progress.run_id === expectedRun)
      && Array.isArray(progress.milestones);
    if (!valid) {
      root.dataset.status = "UNAVAILABLE";
      status.textContent = selected.progressUnavailable;
      identity.textContent = "—";
      if (resume) resume.hidden = true;
      grid.replaceChildren();
      renderRunProgressDetails(null);
      return null;
    }
    root.dataset.status = progress.status;
    if (resume) resume.hidden = true;
    status.textContent = progress.status === "WAITING"
      ? selected.progressWaiting
      : selected.progressStatus[progress.status] || progress.status;
    if (progress.status === "FAILED") {
      const code = typeof progress.failure_code === "string" && /^[A-Z][A-Z0-9_:]{0,127}$/.test(progress.failure_code)
        ? progress.failure_code : selected.progressUnknown;
      status.textContent += ` · ${format(selected.progressFormationFailure, { code })}`;
    }
    identity.textContent = progress.run_id || "—";
    identity.title = progress.run_id || "";
    const fragment = document.createDocumentFragment();
    progress.milestones.forEach((milestone, index) => {
      if (
        !milestone
        || !selected.progressMilestones[milestone.id]
        || !["OBSERVED", "PARTIAL", "WAITING"].includes(milestone.status)
      ) return;
      const article = make("article", `observed-run-step ${milestone.status.toLowerCase()}`);
      const sequence = make("span", "", { text: String(index + 1).padStart(2, "0") });
      const label = make("strong", "", { text: selected.progressMilestones[milestone.id] });
      const state = make("b", "", { text: selected.progressStates[milestone.status] });
      const count = make("small", "", {
        text: milestone.expected_count === null || milestone.expected_count === undefined
          ? language === "en"
            ? `${milestone.observed_count} events`
            : `${milestone.observed_count} 个事件`
          : `${milestone.observed_count}/${milestone.expected_count}`,
      });
      if (milestone.evidence_digest) count.title = milestone.evidence_digest;
      article.append(sequence, label, state, count);
      fragment.append(article);
    });
    grid.replaceChildren(fragment);
    renderRunProgressDetails(progress);
    return progress.status;
  }

  function runReviewEvidence(runId) {
    const view = currentState?.agent_candidate_outputs;
    const panel = byId("agent-work-observability");
    if (stateAvailability !== "ready" || !runId || currentState?.execution?.run_id !== runId
      || view?.run_id !== runId || view.status !== "PASS" || view.candidate_only !== true
      || view.target_writes !== 0 || !Array.isArray(view.outputs)
      || !panel || panel.hidden || panel.dataset.runId !== runId) return [];
    const replans = view.outputs.filter(output => output?.role === "REVIEWER" && output.decision?.verdict === "REPLAN");
    if (!replans.length) return [];
    const domains = new Set(replans.flatMap(output => output.decision.missing_domains || []));
    const reviewers = new Set(replans.map(output => output.actor_id));
    const outputs = [...replans, ...view.outputs.filter(output => output && !replans.includes(output)
      && ((output.role === "REVIEWER" && reviewers.has(output.actor_id))
        || (output.role === "DOMAIN_WORKER" && domains.has(output.domain))))];
    const cards = [...panel.querySelectorAll(".agent-work-card")];
    const targets = [];
    for (const output of outputs) {
      if (typeof output.task_id !== "string" || !output.task_id
        || typeof output.actor_id !== "string" || !output.actor_id
        || !Number.isSafeInteger(output.attempt) || output.attempt < 1
        || !/^sha256:[a-f0-9]{64}$/.test(output.output_digest || "")) return [];
      const matches = cards.filter(card => !card.hidden && card.dataset.agentActor === output.actor_id)
        .flatMap(card => [...card.querySelectorAll(".agent-work-candidate-attempt")]
          .filter(attempt => !attempt.hidden && attempt.dataset.taskId === output.task_id
            && attempt.dataset.attempt === String(output.attempt)
            && attempt.getAttribute("title") === output.output_digest)
          .map(attempt => ({ card, attempt, taskId: output.task_id, digest: output.output_digest })));
      if (matches.length !== 1) return [];
      targets.push(matches[0]);
    }
    return targets;
  }

  function renderRunProgressDetails(progress) {
    const details = byId("observed-run-details");
    const summary = byId("observed-run-details-summary");
    const content = byId("observed-run-details-content");
    if (!details || !summary || !content) return;
    details.hidden = !progress?.run_id;
    if (details.hidden) {
      runProgressDetailsKey = null;
      summary.textContent = "";
      content.replaceChildren();
      return;
    }
    const actions = Array.isArray(progress.actions) ? progress.actions : [];
    const attempts = Array.isArray(progress.reviewer_model_usage) ? progress.reviewer_model_usage : [];
    // Action status is the AgentTeams task/project state; the API verifies its journal.
    const replanned = actions.filter(action => action?.tool === "projectflow" && action.action === "plan_dag"
      && Number.isSafeInteger(action.sequence) && action.sequence > 0).length > 1;
    const reviewTargets = replanned ? runReviewEvidence(progress.run_id) : [];
    const detailKey = JSON.stringify([language, progress.run_id, actions, attempts,
      reviewTargets.map(target => [target.taskId, target.digest])]);
    if (runProgressDetailsKey === detailKey) return;
    runProgressDetailsKey = detailKey;
    const selected = copy();
    const scrollPositions = [];
    const text = value => typeof value === "string" && value ? value : selected.progressUnknown;
    const number = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : selected.progressUnknown;
    summary.textContent = format(selected.progressDetails, { count: actions.length });

    function table(kind, caption, columns) {
      content.querySelector(`[data-progress-empty="${kind}"]`)?.remove();
      let region = content.querySelector(`[data-progress-table="${kind}"]`);
      if (!region) {
        region = make("div", "observed-run-table", { tabindex: "0", role: "region", "data-progress-table": kind });
        if (kind === "actions") content.prepend(region);
        else content.append(region);
      }
      region.setAttribute("aria-label", caption);
      scrollPositions.push([region, region.scrollTop, region.scrollLeft]);
      const element = make("table");
      const head = make("thead");
      const row = make("tr");
      columns.forEach(label => row.append(make("th", "", { scope: "col", text: label })));
      head.append(row);
      const body = make("tbody");
      element.append(make("caption", "", { text: caption }), head, body);
      region.replaceChildren(element);
      return body;
    }

    function empty(kind, message) {
      content.querySelector(`[data-progress-table="${kind}"]`)?.remove();
      let node = content.querySelector(`[data-progress-empty="${kind}"]`);
      if (!node) {
        node = make("p", "", { "data-progress-empty": kind });
        if (kind === "actions") content.prepend(node);
        else content.append(node);
      }
      node.textContent = message;
    }
    if (actions.length) {
      const body = table("actions", selected.progressActionCaption, selected.progressActionColumns);
      let plans = 0;
      actions.forEach(action => {
        if (!action || !Number.isSafeInteger(action.sequence) || action.sequence < 1) return;
        const isPlan = action.tool === "projectflow" && action.action === "plan_dag";
        const replan = isPlan && plans++ > 0;
        const row = make("tr", replan ? "observed-run-replan" : "");
        const actionCell = make("td", "", { text: text(action.action) });
        if (replan) actionCell.append(make("strong", "", { text: selected.progressReplan }));
        row.append(make("td", "", { text: number(action.sequence) }), make("td", "", { text: text(action.tool) }),
          actionCell, make("td", "", { text: text(action.key) }), make("td", "", { text: text(action.status) }));
        body.append(row);
      });
      if (reviewTargets.length) {
        const button = make("button", "button button-quiet", {
          type: "button", text: selected.progressReviewEvidence, "data-run-review-evidence": progress.run_id,
        });
        button.addEventListener("click", () => {
          const targets = runReviewEvidence(progress.run_id);
          if (!targets.length) { button.disabled = true; return; }
          targets.forEach(target => { target.card.open = true; });
          const first = targets[0].attempt;
          first.setAttribute("tabindex", "-1");
          first.focus({ preventScroll: true });
          first.scrollIntoView({ block: "center", behavior: "auto" });
        });
        content.querySelector('[data-progress-table="actions"]').append(button);
      }
    } else empty("actions", selected.progressNoActions);

    if (attempts.length) {
      const body = table("usage", selected.progressUsageCaption, selected.progressUsageColumns);
      attempts.forEach(attempt => {
        if (!attempt || typeof attempt !== "object") return;
        const row = make("tr");
        const task = make("td", "", { text: format(selected.progressUsagePhase, { phase: number(attempt.phase) }) });
        task.append(make("small", "", { text: text(attempt.task_id) }));
        const duration = number(attempt.latency_ms);
        const source = key => selected.progressUsageSources[key] || selected.progressUnknown;
        row.append(task, make("td", "", { text: `${text(attempt.provider)} / ${text(attempt.model_id)}` }),
          make("td", "", { text: number(attempt.input_tokens) }), make("td", "", { text: number(attempt.output_tokens) }),
          make("td", "", { text: duration === selected.progressUnknown ? duration : `${duration} ms` }),
          make("td", "", { text: format(selected.progressUsageSource, { tokens: source(attempt.usage_source), latency: source(attempt.latency_source) }) }));
        body.append(row);
      });
    } else empty("usage", selected.progressNoUsage);
    scrollPositions.forEach(([region, top, left]) => {
      region.scrollTop = top;
      region.scrollLeft = left;
    });
  }

  async function refreshRunProgress({ follow = runProgressFollowing } = {}) {
    clearRunProgressTimer();
    if (follow && Date.now() >= runProgressFollowDeadline) {
      runProgressFollowing = false;
      setRunProgressNotice("progressFollowPaused", {}, { canResume: true });
      return;
    }
    try {
      const progress = await window.OrgRebaseClient.json("/api/workspace/run-progress", { headers: { Accept: "application/json" } });
      const progressStatus = renderRunProgress(progress);
      runProgressRetryCount = 0;
      runProgressFollowing = follow
        && ["WAITING", "RUNNING"].includes(progressStatus)
        && Date.now() < runProgressFollowDeadline;
      if (runProgressFollowing) {
        runProgressTimer = window.setTimeout(
          () => refreshRunProgress({ follow: true }),
          RUN_PROGRESS_POLL_INTERVAL_MS,
        );
      } else if (follow && ["WAITING", "RUNNING"].includes(progressStatus)) {
        setRunProgressNotice("progressFollowPaused", {}, { canResume: true });
      }
    } catch (error) {
      if (error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") {
        runProgressFollowing = false;
        clearRunProgressTimer();
        return;
      }
      if (!follow) {
        runProgressFollowing = false;
        renderRunProgress(null);
        return;
      }
      if (Date.now() >= runProgressFollowDeadline) {
        runProgressFollowing = false;
        setRunProgressNotice("progressFollowPaused", {}, { canResume: true });
        return;
      }
      runProgressRetryCount += 1;
      if (runProgressRetryCount <= RUN_PROGRESS_RETRY_DELAYS_MS.length) {
        runProgressFollowing = true;
        setRunProgressNotice("progressRetrying", {
          attempt: runProgressRetryCount,
          total: RUN_PROGRESS_RETRY_DELAYS_MS.length,
        });
        runProgressTimer = window.setTimeout(
          () => refreshRunProgress({ follow: true }),
          RUN_PROGRESS_RETRY_DELAYS_MS[runProgressRetryCount - 1],
        );
        return;
      }
      runProgressFollowing = false;
      setRunProgressNotice("progressRetryStopped", {}, { canResume: true });
    }
  }

  function followRunProgress() {
    const resume = byId("observed-run-progress-resume");
    if (resume) resume.hidden = true;
    runProgressFollowing = true;
    runProgressRetryCount = 0;
    runProgressFollowDeadline = Date.now() + RUN_PROGRESS_FOLLOW_WINDOW_MS;
    refreshRunProgress({ follow: true });
  }

  function navState(route, state) {
    const stage = state && state.stage || "EMPTY";
    const index = stageIndex(stage);
    const source = state && state.enterprise_data_lineage && state.enterprise_data_lineage.source || {};
    if (route === "quote") {
      if (!organizationContractEstablished(state)) return "waiting";
      return Boolean(state && state.business_complete) ? "done" : (index > 0 || currentRoute === "quote" ? "current" : "ready");
    }
    if (route === "onboarding") return source.status === "ADMITTED_AND_MATCHED" && organizationContractEstablished(state) ? "done" : "current";
    if (route === "assurance") return "audit";
    return "waiting";
  }

  function renderRouteHeader() {
    const selected = copy();
    const descriptor = selected.pages[currentRoute] || selected.pages.quote;
    const title = byId("shell-page-title");
    const summary = byId("shell-page-summary");
    if (title) title.textContent = descriptor[1];
    if (summary) summary.textContent = `${descriptor[0]} · ${descriptor[2]}`;
    document.title = `${descriptor[0]}｜OrgRebase`;
    renderContextFacts(currentState);
  }

  function activate(route, { focus = false } = {}) {
    const next = ROUTES.some((item) => item.id === route) ? route : "onboarding";
    activeLifecycle = next === "onboarding" ? "onboarding" : "daily";
    currentRoute = next;
    const routeNotice = byId("shell-route-notice");
    if (routeNotice) routeNotice.hidden = !invalidRoute;
    pages.forEach((node, id) => {
      const active = id === next;
      node.hidden = !active;
      node.classList.toggle("active", active);
    });
    document.querySelectorAll(".shell-nav-item").forEach((button) => {
      const active = button.dataset.route === next;
      button.classList.toggle("active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
    renderLifecycle();
    renderLifecycleRail(currentState);
    activateShellTab(next, activeTabs[next]);
    renderRouteHeader();
    renderState(currentState);
    // Route changes are page changes, not in-page jumps. Reset immediately so
    // the next workspace never enters midway through a previous long page.
    window.scrollTo({ top: 0, behavior: "auto" });
    if (focus) {
      const heading = byId("shell-page-title");
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
    }
  }

  function navigate(route) {
    const requested = route;
    const next = ROUTE_ALIASES[requested] || requested;
    const requestedTab = SUBROUTE_TABS[requested];
    if (requestedTab) activeTabs[next] = requestedTab;
    const hash = `#/${next}`;
    if (window.location.hash === hash) activate(next, { focus: true });
    else window.location.hash = hash;
  }

  function renderMaterials(state) {
    const selected = copy();
    const root = byId("shell-material-summary");
    const status = byId("shell-material-status");
    const maturity = document.querySelector('[data-shell-copy="materialsKicker"]');
    if (!root || !status || !maturity) return;
    const lineage = state && state.enterprise_data_lineage || {};
    const source = lineage.source || {};
    const scenario = state && state.scenario || {};
    const profile = state && state.enterprise_seed_profile || {};
    const dataClass = String(lineage.data_class || profile.data_class || state && state.boundaries && state.boundaries.data_profile || "").trim();
    const synthetic = scenario.synthetic === true || dataClass === "SYNTHETIC_FIXTURE";
    const components = Array.isArray(source.components) ? source.components : [];
    const values = Array.isArray(source.values) ? source.values : [];
    const observedKinds = new Set(components.map((item) => String(item && item.kind || "").toUpperCase()));
    const ready = source.status === "ADMITTED_AND_MATCHED"
      && components.length === COMPONENTS.length
      && observedKinds.size === COMPONENTS.length
      && COMPONENTS.every((kind) => observedKinds.has(kind))
      && components.every((item) => (
        item && item.completeness === "COMPLETE"
        && Array.isArray(item.source_root_refs)
        && item.source_root_refs.length > 0
        && /^sha256:[0-9a-f]{64}$/.test(String(item.declared_digest || ""))
      ));
    maturity.textContent = stateAvailability === "unavailable"
      ? selected.materialClassUnavailable
      : synthetic
        ? selected.materialClassSynthetic
        : dataClass
          ? format(selected.materialClassObserved, { dataClass })
          : selected.materialClassUnclassified;
    maturity.removeAttribute("title");
    status.textContent = ready ? selected.materialStatusReady : selected.materialStatusWaiting;
    status.dataset.state = ready ? "ready" : "waiting";
    if (!ready) {
      root.innerHTML = `<article class="material-empty"><strong>${html(selected.materialStatusWaiting)}</strong><p>${html(selected.noSource)}</p></article>`;
      return;
    }
    const componentMap = new Map(components.map((item) => [String(item.kind || "").toUpperCase(), item]));
    const componentCards = COMPONENTS.map((kind) => {
      const item = componentMap.get(kind) || {};
      const componentName = selected.componentNames[kind] || kind;
      return `<article class="material-root-card" data-kind="${html(kind)}">
        <div><span>${html(componentName)}</span><b>${html(item.completeness === "COMPLETE" ? selected.componentComplete : item.completeness || selected.noValue)}</b></div>
        <strong>${html(format(selected.componentSource, { component: componentName }))}</strong>
        <small>${html(selected.exactVersionBound)}</small>
      </article>`;
    }).join("");
    const displayValues = [];
    const valueRows = values.map((item, index) => {
      const slot = String(item.slot_id || "").split(/[.:/]/).pop();
      const label = selected.slotNames[slot] || slot || item.object_ref || selected.noValue;
      const sensitivity = selected.sensitivityNames[String(item.sensitivity || "").toUpperCase()] || item.sensitivity || selected.noValue;
      const domainName = selected.domainNames[String(item.domain_id || "").toLowerCase()] || item.domain_id || selected.noValue;
      const rawValue = item.value === true
        ? (language === "en" ? "Yes" : "是")
        : item.value === false
          ? (language === "en" ? "No" : "否")
          : item.value;
      const structured = typeof window.structuredBusinessValue === "function"
        ? window.structuredBusinessValue(item.value, slot, language)
        : item.value !== null && typeof item.value === "object" ? JSON.stringify(item.value) : null;
      const displayValue = structured ?? selected.materialValueNames[String(item.value)] ?? rawValue ?? selected.noValue;
      displayValues.push(displayValue);
      return `<article class="material-value-card">
        <header><span>${html(label)}</span><b>${html(domainName)}</b></header>
        <strong data-material-value="${index}"></strong>
        <dl><div><dt>${html(selected.source)}</dt><dd>${html(format(selected.sourceRegistered, { domain: domainName }))}</dd></div>
        <div><dt>${html(selected.authority)}</dt><dd>${html(format(selected.authorityMatched, { domain: domainName }))}</dd></div>
        <div><dt>${html(selected.sensitivity)}</dt><dd>${html(sensitivity)}</dd></div></dl>
      </article>`;
    }).join("");
    const pricingInputs = typeof admittedPricingInputs === "function" ? admittedPricingInputs(state) : null;
    const pricingSummary = pricingInputs ? `<section class="material-section material-pricing-inputs">
      <header><strong>${html(selected.materialPricingTitle)}</strong></header>
      <div class="material-identity"><article><span>${html(selected.materialBasketInput)}</span><strong>${html(format(selected.materialBasketCount, {count: pricingInputs.lineCount, currency: pricingInputs.currency}))}</strong></article>
      <article><span>${html(selected.materialPolicyInput)}</span><strong>${html(format(selected.materialPolicyRates, {discount: (pricingInputs.discountBps / 100).toFixed(2), tax: (pricingInputs.taxBps / 100).toFixed(2)}))}</strong></article></div>
      <small>${html(selected.materialPricingScope)}</small>
      <details><summary>${html(selected.materialPricingSources)}</summary><dl>
      <dt>${html(selected.materialBasketInput)}</dt><dd>${html(pricingInputs.basketSource)}</dd>
      <dt>${html(selected.materialPolicyInput)}</dt><dd>${html(pricingInputs.policySource)}</dd></dl></details>
    </section>` : "";
    const packRef = String(source.pack_ref || source.profile_ref || "").trim();
    const organizationRef = String(scenario.organization_id || profile.organization_id || "").trim();
    const packDisplayName = String(source.pack_display_name || profile.pack_display_name || "").trim()
      || selected.materialPackNames[packRef]
      || selected.materialPackName;
    const organizationDisplayName = String(scenario.organization_name || profile.organization_name || "").trim()
      || selected.organizationNames[organizationRef]
      || selected.materialOrganizationName;
    root.innerHTML = `
      <section class="material-identity">
        <article><span>${html(selected.pack)}</span><strong>${html(packDisplayName)}</strong><small>${html(selected.loadedByConfig)} · ${html(selected.exactVersionBound)}</small></article>
        <article><span>${html(selected.organization)}</span><strong>${html(organizationDisplayName)}</strong><small>${html(selected.profile)} · ${html(selected.exactVersionBound)}</small></article>
      </section>
      ${pricingSummary}
      <section class="material-section"><header><div><span>${html(selected.fiveRoots)}</span><strong>${html(format(selected.rootCount, { count: components.length }))}</strong></div></header><div class="material-root-grid">${componentCards}</div></section>
      <section class="material-section"><header><div><span>${html(selected.facts)}</span><strong>${html(format(selected.factsCount, { count: values.length }))}</strong></div></header><div class="material-values-grid">${valueRows || `<p>${html(selected.noSource)}</p>`}</div></section>`;
    root.querySelectorAll("[data-material-value]").forEach(node => {
      node.textContent = displayValues[Number(node.dataset.materialValue)];
    });
  }

  function taskScope(state = currentState) {
    const lineage = state && state.enterprise_data_lineage || {};
    const source = lineage.source || {};
    const task = source.task || {};
    const scenario = state && state.scenario || {};
    return {
      actorId: String(task.actor_id || "").trim(),
      customerId: String(task.customer_id || scenario.customer_id || "").trim(),
      deliverableKind: String(task.deliverable_kind || "").trim(),
      templateRef: String(task.template_ref || "").trim(),
      taskRef: String(task.task_ref || scenario.task_id || "").trim(),
    };
  }

  function displayTaskActor(actorId) {
    if (!actorId) return "—";
    if (["employee:enterprise-quote-operator", "employee:sales-owner"].includes(actorId)) return copy().operator;
    return actorId;
  }

  function displayTaskCustomer(customerId) {
    if (!customerId) return "—";
    if (customerId === "customer:blue-harbor") return language === "en" ? "Blue Harbor" : "蓝港客户";
    return customerId;
  }

  function taskIntakeIssues(candidate) {
    const selected = copy();
    const reasons = Array.isArray(candidate && candidate.reason_codes) ? candidate.reason_codes : [];
    const unknowns = Array.isArray(candidate && candidate.unknowns) ? candidate.unknowns : [];
    const values = reasons.map((reason) => selected.taskReasonNames[reason] || selected.taskUnknownFallback);
    if (!values.length) unknowns.forEach((unknown) => values.push(selected.taskUnknownNames[unknown] || selected.taskUnknownFallback));
    return values;
  }

  function safeTaskIntakeError(error) {
    const selected = copy();
    const code = String(error && error.code || "");
    const message = String(error && error.message || "");
    const evidence = `${code} ${message}`.toUpperCase();
    if (evidence.includes("ACTOR") || evidence.includes("IDENTITY")) return selected.taskIdentityError;
    if (evidence.includes("OAC") || evidence.includes("CONTRACT")) return selected.taskContractError;
    return selected.taskError;
  }

  function taskIntakeHeaders(actorId) {
    const headers = { Accept: "application/json", "Content-Type": "application/json" };
    if (actorId) headers["X-OrgRebase-Actor"] = actorId;
    return headers;
  }

  async function taskIntakeApi(path, body, actorId) {
    return window.OrgRebaseClient.json(path, {
      method: "POST",
      headers: taskIntakeHeaders(actorId),
      body,
    });
  }

  function taskWorkDescriptionKey(durableReceipt, actorId) {
    return JSON.stringify([window.OrgRebaseClient.workspace(), durableReceipt.run_id, durableReceipt.digest, actorId]);
  }

  function clearTaskWorkDescription({ clearSubmittedPrompt = false } = {}) {
    const previousText = taskWorkDescription;
    const prompt = byId("task-request-prompt");
    const submittedText = clearSubmittedPrompt && currentState && currentState.stage !== "EMPTY"
      ? prompt?.value.trim() : null;
    ++taskWorkDescriptionGeneration;
    taskWorkDescription = null;
    taskWorkDescriptionRunId = null;
    taskWorkDescriptionContext = null;
    taskWorkDescriptionStatus = "idle";
    const panel = byId("task-intake-private");
    const text = byId("task-intake-private-text");
    if (panel) panel.hidden = true;
    if (text) text.textContent = "";
    if (clearSubmittedPrompt && currentState && currentState.stage !== "EMPTY" && prompt) prompt.value = "";
    const demand = byId("task-compass-demand");
    if (demand && (previousText && demand.textContent === previousText || submittedText && demand.textContent === submittedText)) demand.textContent = "";
  }

  async function loadTaskWorkDescription(state, durableReceipt, actorId) {
    if (!durableReceipt || !actorId || taskWorkDescriptionStatus === "loading") return;
    const generation = taskWorkDescriptionGeneration;
    const workspaceId = window.OrgRebaseClient.workspace();
    const isCurrentTask = () => generation === taskWorkDescriptionGeneration
      && currentState?.execution?.run_id === durableReceipt.run_id
      && currentState.task_intake?.run_id === durableReceipt.run_id
      && currentState.task_intake?.digest === durableReceipt.digest
      && taskScope(currentState).actorId === actorId
      && window.OrgRebaseClient.workspace() === workspaceId;
    if (!isCurrentTask()) return;
    taskWorkDescriptionRunId = durableReceipt.run_id;
    taskWorkDescriptionContext = taskWorkDescriptionKey(durableReceipt, actorId);
    taskWorkDescriptionStatus = "loading";
    taskWorkDescription = null;
    renderTaskIntake(state);
    const headers = taskIntakeHeaders(actorId);
    delete headers["Content-Type"];
    try {
      const payload = await window.OrgRebaseClient.json("/api/workspace/task-intake/work-description", {
        method: "GET",
        headers,
      });
      if (!isCurrentTask() || taskWorkDescriptionRunId !== durableReceipt.run_id) return;
      if (
        !payload
        || payload.schema_version !== "orgrebase.workspace-task-intake-work-description-record.v1"
        || payload.status !== "PRIVATE_RECORD_RETAINED"
        || payload.run_id !== durableReceipt.run_id
        || payload.actor_id !== actorId
        || payload.prompt_digest !== durableReceipt.prompt_digest
        || payload.prompt_length !== durableReceipt.prompt_length
        || payload.candidate_digest !== durableReceipt.candidate_digest
        || payload.approval_digest !== durableReceipt.approval_digest
        || payload.formation_receipt_digest !== durableReceipt.formation_receipt_digest
        || payload.semantic_use !== "TASK_INTENT_ONLY"
        || payload.contributes_business_facts !== false
        || payload.grants_authority !== false
        || payload.included_in_public_evidence !== false
        || payload.included_in_events_or_otlp !== false
        || typeof payload.work_description !== "string"
      ) {
        taskWorkDescriptionStatus = "unavailable";
      } else {
        taskWorkDescription = payload.work_description;
        taskWorkDescriptionStatus = "available";
      }
    } catch (error) {
      if (!isCurrentTask() || taskWorkDescriptionRunId !== durableReceipt.run_id) return;
      taskWorkDescriptionStatus = error.status === 404 ? "not-retained" : "unavailable";
    }
    renderTaskIntake(currentState);
  }

  function setFormedWorkspaceVisibility(state = currentState) {
    const awaitingFormation = !state || state.stage === "EMPTY";
    const quotePage = pages.get("quote");
    if (!quotePage) return;
    [quotePage.querySelector(".journey"), byId("command-bar"), byId("workspace")].forEach((node) => {
      if (!node) return;
      node.hidden = awaitingFormation;
      node.setAttribute("aria-hidden", awaitingFormation ? "true" : "false");
    });
    const legacyPrimaryAction = byId("primary-action");
    if (legacyPrimaryAction && awaitingFormation) legacyPrimaryAction.disabled = true;
  }

  const DIGEST_PATTERN = /^sha256:[0-9a-f]{64}$/;

  function persistedTaskIntake(state) {
    const receipt = state && state.task_intake;
    const runId = state && state.execution && state.execution.run_id;
    if (!receipt || !runId || receipt.run_id !== runId) return null;
    if (
      receipt.schema_version !== "orgrebase.workspace-task-intake-run-receipt.v1"
      || receipt.status !== "FORMATION_COMPLETED"
      || receipt.intake_persisted !== true
      || receipt.intake_canonical_target_writes !== 0
      || receipt.formation_authority !== "ORGREBASE_CONTROL_PLANE"
      || receipt.claim_boundary !== "INTAKE_GATE_VERIFIED_THEN_EXISTING_CONTROL_PLANE_FORMED_QUOTE"
      || !Number.isSafeInteger(receipt.prompt_length)
      || receipt.prompt_length < 0
      || typeof receipt.quote_ref !== "string"
      || !receipt.quote_ref
      || typeof receipt.artifact_id !== "string"
      || !receipt.artifact_id
    ) return null;
    const requiredDigests = [
      receipt.digest,
      receipt.prompt_digest,
      receipt.candidate_digest,
      receipt.approval_digest,
      receipt.task_digest,
      receipt.formation_receipt_digest,
      receipt.artifact_payload_digest,
      receipt.event_digest,
    ];
    if (!requiredDigests.every((value) => DIGEST_PATTERN.test(String(value || "")))) return null;
    if (
      receipt.oac_activation_binding_digest != null
      && !DIGEST_PATTERN.test(String(receipt.oac_activation_binding_digest))
    ) return null;
    return receipt;
  }

  function renderTaskIntake(state = currentState) {
    const selected = copy();
    const root = byId("task-intake");
    const prompt = byId("task-request-prompt");
    const prepare = byId("task-intake-prepare");
    const candidatePanel = byId("task-intake-candidate");
    const admit = byId("task-intake-admit");
    const run = byId("task-intake-run");
    const privatePanel = byId("task-intake-private");
    const privateText = byId("task-intake-private-text");
    const contract = byId("task-intake-contract");
    const error = byId("task-intake-error");
    if (!root || !prompt || !prepare || !candidatePanel || !admit || !run || !privatePanel || !privateText || !contract || !error) return;

    const stateReady = stateAvailability === "ready" && state && STAGES.includes(state.stage);
    if (!stateReady) {
      const unavailable = stateAvailability === "unavailable";
      const status = unavailable ? selected.workspaceUnavailable : selected.workspaceLoading;
      root.dataset.contract = "blocked";
      root.dataset.complete = "false";
      root.dataset.availability = unavailable ? "unavailable" : "loading";
      contract.textContent = status;
      prompt.placeholder = selected.taskPromptExample;
      prompt.disabled = true;
      prepare.disabled = true;
      prepare.textContent = status;
      candidatePanel.hidden = false;
      candidatePanel.dataset.status = "hold";
      setExactText(byId("task-intake-candidate-status"), status);
      setExactText(byId("task-intake-candidate-badge"), status);
      setExactText(byId("task-intake-template"), "—");
      setExactText(byId("task-intake-digest"), "—");
      setExactText(byId("task-intake-unknowns"), status);
      admit.hidden = true;
      admit.disabled = true;
      run.hidden = true;
      run.disabled = true;
      error.hidden = true;
      error.textContent = "";
      privatePanel.hidden = true;
      setFormedWorkspaceVisibility(null);
      return;
    }

    const scope = taskScope(state);
    const formed = state.stage !== "EMPTY";
    if (!formed && taskWorkDescriptionRunId !== null) {
      clearTaskWorkDescription();
    }
    const durableReceipt = formed ? persistedTaskIntake(state) : null;
    const intakeEvidenceMissing = formed && !durableReceipt;
    const contractReady = organizationContractReady(state);
    const contractEstablished = organizationContractEstablished(state);
    const hasExactScope = Boolean(scope.actorId && scope.customerId && scope.deliverableKind);
    const session = window.OrgRebaseClient.session();
    const operatorAllowed = !session?.authentication_required || session.authenticated
      && session.principal?.actor_id === scope.actorId;
    const operatorHint = language === "en" ? `Required operator: ${displayTaskActor(scope.actorId)}`
      : `需由${displayTaskActor(scope.actorId)}发起`;
    for (const button of [prepare, admit, run]) {
      if (!operatorAllowed) button.title = operatorHint;
      else button.removeAttribute("title");
    }
    delete root.dataset.availability;
    root.dataset.contract = contractEstablished ? "ready" : "blocked";
    root.dataset.complete = formed ? "true" : "false";
    byId("task-intake-kicker").textContent = formed ? durableReceipt ? selected.taskCompletedKicker : selected.taskUnverifiedKicker : selected.taskIntakeKicker;
    byId("task-intake-title").textContent = formed ? durableReceipt ? selected.taskCompletedTitle : selected.taskUnverifiedTitle : selected.taskIntakeTitle;
    byId("task-intake-body").textContent = formed ? durableReceipt ? selected.taskCompletedBody : selected.taskEvidenceUnavailableDetail : selected.taskIntakeBody;
    contract.textContent = contractEstablished ? selected.taskContractReady : selected.taskContractBlocked;
    prompt.placeholder = selected.taskPromptExample;
    prompt.disabled = taskIntakeBusy || formed || !contractReady || !operatorAllowed;
    privatePanel.hidden = !formed;
    if (formed) {
      if (!operatorAllowed) {
        clearTaskWorkDescription();
        taskWorkDescriptionStatus = "restricted";
      } else if (!durableReceipt) {
        clearTaskWorkDescription();
        taskWorkDescriptionStatus = "not-retained";
      } else if (taskWorkDescriptionContext !== taskWorkDescriptionKey(durableReceipt, scope.actorId)) {
        clearTaskWorkDescription();
        taskWorkDescriptionRunId = durableReceipt.run_id;
        taskWorkDescriptionContext = taskWorkDescriptionKey(durableReceipt, scope.actorId);
      }
      privatePanel.hidden = false;
      privatePanel.dataset.status = taskWorkDescriptionStatus;
      privateText.textContent = taskWorkDescriptionStatus === "available"
        ? taskWorkDescription
        : taskWorkDescriptionStatus === "restricted"
          ? selected.taskPrivateRestricted
          : taskWorkDescriptionStatus === "not-retained"
          ? selected.taskPrivateNotRetained
          : taskWorkDescriptionStatus === "unavailable"
            ? selected.taskPrivateUnavailable
            : selected.taskPrivateLoading;
      if (operatorAllowed && durableReceipt && taskWorkDescriptionStatus === "idle") {
        const generation = taskWorkDescriptionGeneration;
        window.setTimeout(() => {
          if (generation === taskWorkDescriptionGeneration) loadTaskWorkDescription(state, durableReceipt, scope.actorId);
        }, 0);
      }
    }
    byId("task-intake-actor").textContent = displayTaskActor(scope.actorId);
    byId("task-intake-actor").removeAttribute("title");
    byId("task-intake-customer").textContent = displayTaskCustomer(scope.customerId);
    byId("task-intake-customer").removeAttribute("title");
    prepare.disabled = taskIntakeBusy || formed || !operatorAllowed || Boolean(taskCandidate) || (contractReady && (!hasExactScope || prompt.value.trim().length < 8));
    prepare.textContent = !contractReady
      ? selected.taskCompleteOnboarding
      : taskIntakeBusy && taskIntakeAction === "prepare"
        ? selected.taskPreparing
        : selected.taskPrepare;
    error.hidden = !taskIntakeError;
    error.textContent = taskIntakeError || "";
    setFormedWorkspaceVisibility(state);

    if (!taskCandidate && !formed) {
      candidatePanel.hidden = true;
      return;
    }

    candidatePanel.hidden = false;
    const candidateReady = Boolean(taskCandidate && taskCandidate.status === "READY_FOR_CONFIRMATION");
    const candidateHold = Boolean(taskCandidate && taskCandidate.status === "HOLD");
    const status = formed
      ? (intakeEvidenceMissing ? selected.taskEvidenceUnavailable : selected.taskStarted)
      : taskApproval
        ? (taskIntakeError && !taskIntakeBusy ? selected.taskAdmittedRetry : selected.taskAdmitted)
        : candidateReady
          ? selected.taskCandidateReady
          : selected.taskCandidateHold;
    candidatePanel.dataset.status = formed ? (intakeEvidenceMissing ? "hold" : "complete") : candidateHold ? "hold" : "ready";
    setExactText(
      byId("task-intake-candidate-status"),
      status,
      durableReceipt
        ? [durableReceipt.run_id, durableReceipt.artifact_id, durableReceipt.event_digest].join(" · ")
        : null,
    );

    const promptDigest = durableReceipt && durableReceipt.prompt_digest || taskCandidate && taskCandidate.prompt_digest;
    const promptLength = durableReceipt
      ? durableReceipt.prompt_length
      : taskCandidate && taskCandidate.prompt_length;
    const inputProof = promptDigest
      ? format(selected.taskInputProof, { length: Number(promptLength || 0) })
      : (formed ? selected.taskEvidenceUnavailable : selected.taskStartedBadge);
    setExactText(byId("task-intake-candidate-badge"), inputProof, promptDigest);
    const templateRef = formed
      ? scope.templateRef
      : taskCandidate && taskCandidate.template_ref || scope.templateRef;
    setExactText(byId("task-intake-template"), templateRef ? selected.taskTemplateValue : "—", templateRef || null);

    const binding = durableReceipt
      ? selected.taskChainProof
      : formed
        ? "—"
        : taskApproval
          ? (taskIntakeError && !taskIntakeBusy ? selected.taskAdmittedRetryProof : selected.taskAdmittedProof)
          : candidateHold
            ? selected.taskHoldProof
            : taskCandidate && taskCandidate.digest ? selected.taskCandidateProof : null;
    setExactText(
      byId("task-intake-digest"),
      binding || "—",
      durableReceipt
        ? [
            durableReceipt.digest,
            durableReceipt.candidate_digest,
            durableReceipt.approval_digest,
            durableReceipt.task_digest,
            durableReceipt.formation_receipt_digest,
            durableReceipt.artifact_payload_digest,
            durableReceipt.event_digest,
          ].join(" · ")
        : binding,
    );
    const activationDigest = durableReceipt && durableReceipt.oac_activation_binding_digest
      || (!formed && taskCandidate && taskCandidate.oac_activation_binding_digest);
    const issues = taskIntakeIssues(taskCandidate);
    byId("task-intake-third-label").textContent = formed
      ? selected.taskConstraintLabel
      : candidateHold
        ? selected.taskHoldReasonLabel
        : selected.taskStartPrerequisitesLabel;
    byId("task-intake-unknowns").textContent = formed
      ? (intakeEvidenceMissing
        ? selected.taskEvidenceUnavailableDetail
        : activationDigest
          ? selected.taskOacBound
          : selected.taskOacOptional)
      : (issues.length ? issues.join("；") : selected.taskNoUnknowns);
    byId("task-intake-unknowns").removeAttribute("title");
    admit.hidden = formed;
    admit.disabled = taskIntakeBusy || !operatorAllowed || !candidateReady || candidateHold || Boolean(taskApproval);
    admit.textContent = taskApproval
      ? selected.taskAdmittedButton
      : taskIntakeBusy && taskIntakeAction === "admit"
        ? selected.taskAdmitting
        : selected.taskAdmit;
    run.hidden = formed || !taskApproval;
    run.disabled = taskIntakeBusy || !operatorAllowed || !taskApproval;
    run.textContent = taskIntakeBusy && taskIntakeAction === "run"
      ? selected.taskRunning
      : selected.taskRun;
  }

  async function prepareTaskIntake() {
    if (taskIntakeBusy || currentState && currentState.stage !== "EMPTY") return;
    if (!organizationContractReady(currentState)) {
      navigate("oac");
      return;
    }
    const prompt = byId("task-request-prompt");
    const scope = taskScope(currentState);
    if (!prompt || !prompt.value.trim() || !scope.actorId || !scope.customerId || !scope.deliverableKind) return;
    taskCandidate = null;
    taskApproval = null;
    taskIntakeError = null;
    taskIntakeBusy = true;
    taskIntakeAction = "prepare";
    renderTaskIntake(currentState);
    try {
      taskCandidate = await taskIntakeApi("/api/workspace/task-intake/prepare", {
        prompt: prompt.value,
        actor_id: scope.actorId,
        customer_id: scope.customerId,
        deliverable_kind: scope.deliverableKind,
      }, scope.actorId);
    } catch (requestError) {
      taskIntakeError = safeTaskIntakeError(requestError);
    } finally {
      taskIntakeBusy = false;
      taskIntakeAction = "idle";
      renderTaskIntake(currentState);
      renderContextFacts(currentState);
      renderOverviewFlow(currentState);
    }
  }

  async function admitTaskIntake() {
    if (taskIntakeBusy || !taskCandidate || taskCandidate.status !== "READY_FOR_CONFIRMATION") return;
    const scope = taskScope(currentState);
    if (taskApproval) return;
    taskIntakeError = null;
    taskIntakeBusy = true;
    taskIntakeAction = "admit";
    renderTaskIntake(currentState);
    try {
      taskApproval = await taskIntakeApi("/api/workspace/task-intake/admit", {
        actor_id: scope.actorId,
        candidate_receipt: taskCandidate,
        candidate_digest: taskCandidate.digest,
      }, scope.actorId);
    } catch (requestError) {
      taskIntakeError = safeTaskIntakeError(requestError);
    } finally {
      taskIntakeBusy = false;
      taskIntakeAction = "idle";
      renderTaskIntake(currentState);
    }
  }

  async function runTaskIntake() {
    if (
      taskIntakeBusy
      || !taskCandidate
      || taskCandidate.status !== "READY_FOR_CONFIRMATION"
      || !taskApproval
    ) return;
    const scope = taskScope(currentState);
    const prompt = byId("task-request-prompt");
    if (!prompt || !prompt.value) return;
    taskIntakeError = null;
    taskIntakeBusy = true;
    taskIntakeAction = "run";
    const requestWorkspace = window.OrgRebaseClient.workspace();
    const requestedCandidate = taskCandidate;
    const requestedApproval = taskApproval;
    const requestedRun = requestedCandidate.intended_run_id;
    renderTaskIntake(currentState);
    activateShellTab("quote", "collaboration", { focus: true });
    followRunProgress();
    try {
      const result = await taskIntakeApi("/api/workspace/task-intake/run", {
        actor_id: scope.actorId,
        candidate_receipt: taskCandidate,
        candidate_digest: taskCandidate.digest,
        approval_receipt: taskApproval,
        approval_digest: taskApproval.digest,
        work_description: prompt.value,
      }, scope.actorId);
      const formedState = result && result.state && typeof result.state === "object"
        ? result.state
        : result && typeof result === "object" && STAGES.includes(result.stage)
          ? result
          : null;
      const receipt = persistedTaskIntake(formedState);
      const showResult = receipt && formedState.quote
        && requestedRun && currentState?.execution?.run_id === requestedRun
        && receipt.run_id === requestedRun && receipt.actor_id === scope.actorId
        && receipt.candidate_digest === requestedCandidate.digest
        && receipt.approval_digest === requestedApproval.digest
        && requestedApproval.intended_run_id === requestedRun
        && requestedCandidate.workspace_instance_nonce
        && requestedApproval.workspace_instance_nonce === requestedCandidate.workspace_instance_nonce
        && taskCandidate === requestedCandidate && taskApproval === requestedApproval
        && window.OrgRebaseClient.workspace() === requestWorkspace;
      window.dispatchEvent(new CustomEvent("orgrebase:workspacechange", {
        detail: { stage: formedState && formedState.stage || "CURRENT", source: "task-intake" },
      }));
      if (showResult && currentRoute === "quote") {
        activateShellTab("quote", "work", { focus: true });
      }
    } catch (requestError) {
      if (requestError.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
      taskIntakeError = safeTaskIntakeError(requestError);
    } finally {
      taskIntakeBusy = false;
      taskIntakeAction = "idle";
      renderTaskIntake(currentState);
      if (taskIntakeError) focusTaskIntakeError();
      await refreshRunProgress({ follow: runProgressFollowing });
    }
  }

  function focusTaskIntakeError() {
    const message = taskIntakeError;
    const focusError = () => {
      if (taskIntakeError !== message || currentRoute !== "quote") return;
      activateShellTab("quote", "work");
      const error = byId("task-intake-error");
      if (!error || error.hidden) return;
      error.focus();
      error.scrollIntoView({ block: "center", behavior: "auto" });
    };
    const changingHash = window.location.hash !== "#/quote";
    // Let the normal hashchange handler activate the page before moving focus.
    if (changingHash) window.addEventListener("hashchange", focusError, { once: true });
    navigate("quote");
    if (!changingHash) focusError();
  }

  function bindTaskIntake() {
    const prompt = byId("task-request-prompt");
    const prepare = byId("task-intake-prepare");
    const admit = byId("task-intake-admit");
    const run = byId("task-intake-run");
    if (!prompt || !prepare || !admit || !run || prepare.dataset.bound) return;
    prepare.dataset.bound = "true";
    prepare.addEventListener("click", prepareTaskIntake);
    admit.addEventListener("click", admitTaskIntake);
    run.addEventListener("click", runTaskIntake);
    prompt.addEventListener("input", () => {
      if (taskIntakeBusy) return;
      taskCandidate = null;
      taskApproval = null;
      taskIntakeError = null;
      renderTaskIntake(currentState);
    });
  }

  function focusTaskIntake() {
    navigate("quote");
    activateShellTab("quote", "work");
    window.setTimeout(() => {
      const panel = byId("task-intake");
      const prompt = byId("task-request-prompt");
      if (panel && typeof panel.scrollIntoView === "function") {
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      if (prompt && !prompt.disabled && typeof prompt.focus === "function") {
        prompt.focus({ preventScroll: true });
      }
    }, 0);
  }

  function renderState(state, { availability = null } = {}) {
    const previousRun = currentState?.execution?.run_id;
    if (state && STAGES.includes(state.stage)) {
      currentState = state;
      stateAvailability = "ready";
    } else if (state != null) {
      currentState = null;
      stateAvailability = "unavailable";
    } else if (!currentState && availability === "unavailable") {
      stateAvailability = "unavailable";
    } else if (!currentState && stateAvailability !== "unavailable") {
      stateAvailability = "loading";
    }
    const selected = copy();
    const projectedState = stateAvailability === "ready" ? currentState : null;
    if (!projectedState || previousRun !== projectedState.execution?.run_id) renderRunProgress(null);
    renderContextFacts(projectedState);
    renderElementObservation(projectedState);
    ROUTES.forEach((route) => {
      const button = document.querySelector(`.shell-nav-item[data-route="${route.id}"]`);
      if (!button) return;
      const status = projectedState ? navState(route.id, projectedState) : stateAvailability;
      const label = button.querySelector(".shell-nav-state");
      label.textContent = selected.navState[status];
      label.dataset.state = status;
    });
    renderOverviewFlow(projectedState);
    renderMaterials(projectedState);
    renderTaskPrerequisite(projectedState);
    renderLifecycleRail(projectedState);
    renderTaskIntake(projectedState);
  }

  function boot() {
    buildShell();
    if (!shell) return;
    positionCollaborationContext();
    positionPlatformGovernance();
    bindTaskIntake();
    setCopy();
    if (!window.location.hash) window.history.replaceState(null, "", "#/onboarding");
    currentRoute = routeFromHash();
    activate(currentRoute);
    window.addEventListener("hashchange", () => activate(routeFromHash(), { focus: true }));
    window.addEventListener("orgrebase:languagechange", (event) => {
      language = event.detail && event.detail.language === "en" ? "en" : "zh-CN";
      setCopy();
      refreshRunProgress({ follow: runProgressFollowing });
    });
    window.addEventListener("orgrebase:sessionended", (event) => {
      clearTaskWorkDescription({ clearSubmittedPrompt: true });
      currentState = null;
      if (["logout", "role-switch"].includes(event.detail?.reason)) {
        taskCandidate = null;
        taskApproval = null;
        const prompt = byId("task-request-prompt");
        if (prompt) prompt.value = "";
      }
      renderState(null, { availability: "unavailable" });
    });
    window.addEventListener("orgrebase:staterendered", (event) => {
      renderState(event.detail || null);
      refreshRunProgress({ follow: runProgressFollowing });
    });
    window.addEventListener("orgrebase:stateunavailable", (event) => {
      clearTaskWorkDescription({ clearSubmittedPrompt: true });
      if (event?.detail?.reason === "identity-changed") {
        taskCandidate = null;
        taskApproval = null;
        taskIntakeError = null;
        const prompt = byId("task-request-prompt");
        if (prompt) prompt.value = "";
      }
      currentState = null;
      renderState(null, { availability: "unavailable" });
    });
    refreshRunProgress({ follow: false });
  }

  window.OrgRebaseWorkspaceShell = Object.freeze({
    navigate,
    focusTaskIntake,
    selectTab: activateShellTab,
    currentRoute: () => currentRoute,
  });
  boot();
})();
