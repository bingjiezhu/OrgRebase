(() => {
  "use strict";

  const API = Object.freeze({
    status: "/api/workspace/oac-adaptation/agentic",
    agentPrepare: "/api/workspace/oac-adaptation/agent-prepare",
    structuredPrepare: "/api/workspace/oac-adaptation/prepare",
    approve: "/api/workspace/oac-adaptation/approve",
    executeShadow: "/api/workspace/oac-adaptation/execute-shadow",
    actorHeader: "X-OrgRebase-Actor",
    defaultOwner: "human:workspace-owner",
  });

  const SOURCE_KINDS = Object.freeze(["DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY"]);
  const STEP_IDS = Object.freeze(["inputs", "mapping", "validation", "admission", "activation", "consumption"]);
  const REQUIRED_OWNER_ACKNOWLEDGEMENTS = Object.freeze([
    "REVIEWED_SOURCE_TO_CONTRACT_SUMMARY",
    "ACCEPTED_DECLARED_UNKNOWNS",
    "UNDERSTAND_NO_BUSINESS_APPROVAL",
  ]);
  const POLICY_MODES = Object.freeze(["FROZEN_REPLAY", "OFFLINE_LOCAL", "LIVE_VERTEX"]);
  const POLICY_ACTIONS = Object.freeze({ prepare: "AGENT_PREPARE", structured: "STRUCTURED_PREPARE", shadow: "EXECUTE_SHADOW" });
  const WORKSPACE_GATE_STATUSES = Object.freeze([
    "BLOCKED_PENDING_OAC",
    "READY_TO_FORM",
    "CONSUMED_BY_QUOTE_FORMATION",
  ]);

  const COPY = Object.freeze({
    "zh-CN": Object.freeze({
      kicker: "企业首次接入 · 组织契约适配",
      title: "让接入智能体按 OAC 把企业材料编译为可验证工作区",
      reviewJump: "查看审阅与操作",
      introLabel: "组织智能体契约（OAC）企业适配层",
      introTitle: "智能体降低适配成本，契约和人工负责人保留最终权威",
      introBodyByMode: {
        FROZEN_REPLAY: "OAC 定义智能体必须遵守的输入、权限、交接与准入规则；接入智能体只生成候选，确定性校验与指定负责人决定是否放行。当前仅重放并重新验证封存回执，不发起新模型调用。材料性质以后端审阅摘要为准。",
        OFFLINE_LOCAL: "OAC 定义智能体必须遵守的输入、权限、交接与准入规则；接入智能体只生成候选，确定性校验与指定负责人决定是否放行。当前只执行后端策略明确开放的本地动作。材料性质以后端审阅摘要为准。",
        LIVE_VERTEX: "OAC 定义智能体必须遵守的输入、权限、交接与准入规则；接入智能体只生成候选，确定性校验与指定负责人决定是否放行。只有后端策略明确开放时，才会发起新的 Vertex 候选映射或增强验收。材料性质以后端审阅摘要为准。",
        UNKNOWN_SAFE: "OAC 定义智能体必须遵守的输入、权限、交接与准入规则；接入智能体只生成候选。后端未提供可验证的执行策略或业务启动门，页面已保持关闭。",
      },
      steps: ["企业材料", "候选映射", "OAC 契约校验", "负责人审阅与准入", "契约激活", "业务消费"],
      stepDetails: ["领域 · 知识 · 权威 · 能力 · 依赖", "接入智能体只生成建议", "来源 · 需求 · 权威 · 未知项", "业务摘要 · 指定负责人", "精确绑定当前工作区", "后续变化运行引用已激活契约"],
      sourceNames: { DOMAIN: "领域", KNOWLEDGE: "知识", AUTHORITY: "权威", CAPABILITY: "能力", DEPENDENCY: "依赖" },
      sourceCandidate: "候选映射",
      sourceGridLabel: "五类企业输入的技术绑定",
      sourceWaiting: "等待映射",
      sourceComplete: "已映射",
      sourceBlocked: "有缺口",
      statusLoading: "正在读取适配状态",
      statusUnavailable: "适配服务尚未就绪",
      status: {
        NOT_STARTED: "企业材料已就绪",
        AGENT_MAPPING_HOLD: "接入智能体保留了未解决缺口",
        OWNER_REVIEW_PENDING: "等待企业合同负责人准入",
        READY_FOR_SHADOW: "契约已准入并激活，可进入持续演化工作台",
        SHADOW_COMPLETED: "契约已激活，增强验收已完成",
      },
      runLabel: "适配运行",
      runDetailSeparate: "独立适配任务，不影响当前业务运行",
      runDetailRequiredPending: "强制前置门；等待精确激活绑定",
      runDetailBound: "已精确绑定当前工作区运行",
      runDetailConsumed: "已被本次报价形成事务消费",
      contractLabel: "OAC 契约绑定",
      contractDetail: "来源 / 需求 / 准入摘要 · 审阅门 {gate} · 人工等待 {wait} · 批准 {approval}",
      gapLabel: "缺口与原因",
      gapChecking: "正在检查",
      gapUnavailable: "缺口信息未完整报告",
      gapNone: "0 个缺口",
      gapCount: "{count} 个缺口",
      productGapLabel: "企业事实缺口",
      productGapClear: "必需事实已完整映射",
      productGapBlocked: "系统已停下，等待补齐",
      productGapWaiting: "尚未完成候选映射",
      productAdmissionLabel: "人工准入",
      productAdmissionComplete: "契约与人工准入已完成",
      productAdmissionPending: "等待指定负责人",
      productAdmissionBlocked: "缺口未清零 · 不可准入",
      productAdmissionWaiting: "等待契约校验",
      productAdmissionDetail: "只有指定负责人可批准",
      productValidationLabel: "契约校验",
      productValidationWaiting: "等待候选",
      productValidationUnknown: "校验结果未完整报告",
      productValidationPass: "五类材料已通过确定性校验",
      productValidationHold: "校验保留未知项",
      productValidationDetail: "来源、权威和需求必须精确对应",
      productActivationLabel: "契约激活",
      productActivationWaiting: "等待人工准入",
      productActivationReady: "契约已激活",
      productActivationConsumed: "已被当前业务任务消费",
      productActivationDetail: "激活后才能进入持续演化工作台",
      productShadowLabel: "影子运行",
      productShadowReady: "已准入 · 等待影子运行",
      productShadowNotRun: "等待人工准入",
      productShadowDetail: "验证适配结果，不影响正式状态",
      productEffectLabel: "当前效应边界",
      productOutcomesLabel: "OAC 企业适配结果",
      productBusinessLabel: "业务启动门",
      businessBlocked: "报价尚未放行",
      businessReady: "报价可以形成",
      businessConsumed: "本次报价已消费 OAC",
      businessOptional: "OAC 为可选附加验证",
      businessBlockedDetail: "组织契约未准入；服务端不允许形成内部工作稿",
      businessReadyDetail: "激活绑定已锁定当前工作区；下一步可形成内部工作稿",
      businessConsumedDetail: "协作工作稿形成事务已持久化激活绑定与消费回执",
      businessConsumedOptionalDetail: "本次报价已精确绑定并消费 OAC；当前部署策略未强制所有任务使用 OAC",
      businessOptionalDetail: "当前工作区未强制 OAC 准入；报价流程保持可用",
      businessUnknownDetail: "业务启动门不完整；为避免绕过契约，业务已保持关闭",
      businessLineageInvalid: "契约证据不完整，已停止使用",
      businessLineageInvalidDetail: "当前状态声称已激活，但负责人摘要、批准或激活绑定不完整；系统已关闭业务消费",
      causalInputs: "{count} 类企业输入",
      causalDomains: "{count} 个领域",
      causalContext: "上下文已绑定",
      causalHuman: "人工门已通过",
      causalTool: "Tool 已入证据链",
      causalSkill: "Skill 已入证据链",
      causalWaiting: "因果证据将随真实执行逐步出现",
      auditTitle: "高级审计证据",
      auditDetail: "摘要、内部路径、运行绑定与技术回执；常规审阅无需阅读",
      effectLabel: "效应上限",
      effectZero: "零外部效应",
      effectDetail: "规范写入 {writes} 次 · 候选不等于正式状态",
      effectDeclared: "声明上限：禁止外部写入",
      effectDeclaredDetail: "尚未执行。这里显示契约允许的范围，实际写入结果待执行后核对",
      agentLabel: "接入智能体",
      agentNotRun: "等待生成候选",
      agentVerified: "{suggestion}已验证",
      agentHold: "默认拒绝 · 未准入",
      agentDetail: "{runtime} · {actions} 个原生动作 · {model}",
      productAgentWaiting: "真实模型回执与 AgentTeams 原生动作将在候选形成后显示",
      structuredLabel: "候选生成方式",
      structuredReady: "已从配置材料编译候选",
      structuredWaiting: "等待编译配置材料",
      structuredDetail: "确定性编译，不调用映射模型；候选仍需契约校验与指定负责人准入。",
      providerVertexLive: "真实 Vertex 模型建议",
      providerLocal: "本地模型建议",
      providerStructured: "结构化材料 · 确定性编译",
      providerIncomplete: "映射证据不完整",
      mappingRateLimited: "模型服务暂不可用（限流）",
      mappingServiceFailed: "模型服务请求未成功",
      mappingSchemaFailed: "模型候选结构未通过检查",
      mappingNoCandidate: "本次未产生可准入候选",
      mappingNotAssessed: "尚未评估企业事实缺口",
      mappingFailureRecovery: "等待服务恢复或排查项目额度后，由管理员保留失败记录并新建工作区重试；刷新只查看已有记录，不会重发请求。",
      providerReceipt: "模型建议回执",
      modelStatuses: { VALID: "已验证", NOT_RUN: "等待模型候选", HOLD: "已保留", ABSTAIN: "主动放弃交付", PROVIDER_ERROR: "模型服务失败", SCHEMA_ERROR: "结构校验失败" },
      bindingLabel: "运行绑定",
      bindingNone: "尚未绑定",
      bindingDetail: "适配运行 → 适配胶囊 → 执行运行",
      bindingReady: "胶囊已准入 · 等待影子运行",
      bindingConsumed: "激活绑定 {activation} · 消费回执 {consumption}",
      independentVerified: "独立复核 {outputs} 个输出 / {packs} 个输入文件 / 0 失败",
      layerNames: { SOURCE: "数据源", CONTEXT: "上下文", AGENTTEAMS: "AgentTeams", TOOL: "工具", SKILL: "Skill", OTLP: "可观测数据", CANDIDATE: "候选结果" },
      parityDetail: "{obligations} 个稳定义务 → {attempts} 个动态任务尝试 · 财务主动放弃交付→重试 · 审查智能体重新规划→验收通过",
      ownerLabel: "人工权威",
      ownerEvergreen: "常青工业工作区负责人",
      ownerWorkspace: "企业工作台负责人",
      controlNote: "倒计时只是审阅提示；服务端最短审阅门、三项确认和精确摘要共同构成准入权威。",
      refresh: "刷新适配状态",
      refreshing: "读取中…",
      prepare: "准备候选适配",
      prepareReplay: "验证接入候选",
      prepareOffline: "运行本地候选适配",
      prepareStructured: "从已配置材料生成契约候选",
      prepareLive: "运行新的 Vertex 候选映射",
      preparing: "正在准备候选适配…",
      review: "请审阅适配 · {seconds}秒",
      approve: "批准并激活组织契约",
      approving: "正在绑定人工准入…",
      executeShadow: "执行 OAC 绑定影子运行",
      shadowReplay: "重放并验证影子运行回执",
      shadowOffline: "运行本地影子验证",
      shadowLive: "运行新的 Vertex 影子验证",
      continueToQuote: "进入报价工作台",
      shadowOptional: "可选影子复验 · 业务启动门已放行",
      actionDisabled: "当前模式未开放此动作",
      executingShadow: "正在执行影子运行…",
      runIndependentVerification: "执行独立复验",
      runningIndependentVerification: "正在执行独立复验…",
      shadowCompletedPending: "影子运行已完成 · 独立复核待执行",
      shadowVerified: "影子运行与独立复核已通过",
      held: "缺口未清零 · 不可准入",
      rejected: "已拒绝 · 不可运行",
      contextKicker: "上下文住在哪里",
      contextTitle: "任务协调器只持有运行摘要，领域智能体只接收本次必需内容",
      contextWaiting: "等待人工准入后编译",
      contextUnanchored: "历史上下文缺少可核对的来源记录，暂不可用于执行",
      contextCompiled: "上下文已精确绑定",
      managerTitle: "确定性任务协调器 · 管理角色",
      managerDetail: "目标：{objective} · {obligations} 类证据义务 · 最小团队",
      managerFormationBound: "组队决策收据已绑定",
      managerContextBound: "组织意图与组队决策已绑定",
      topologyExact: "组队计划选择 {planned} 个领域 · AgentTeams 实际创建 {actual} 个 · 精确一致",
      topologyBound: "组队决策已绑定；实际 AgentTeams 拓扑以当前运行回执为准",
      topologyPending: "等待组队计划编译为实际 AgentTeams 任务",
      residentTitle: "领域常驻契约",
      residentDetail: "{count} 个领域 · 权威源类型 · 允许的工具 / Skill · 退让规则",
      taskTitle: "本次任务投影",
      taskDetail: "{count} 份最小投影 · 用途 · 接收者 · 过期时间 · 修订锁",
      returnTitle: "结果回收",
      returnDetail: "候选结果 → 审查智能体 → 确定性控制面",
      writesZero: "规范写入 0",
      policyBoundary: "当前模式：{mode} · 候选来源：{source}。下一步外部调用边界：{calls}页面只把已观测的模型回执显示为真实运行；接入智能体只提议，OAC 校验与指定负责人决定是否激活。",
      policyModes: { FROZEN_REPLAY: "冻结证据回放", OFFLINE_LOCAL: "本地交互演示", LIVE_VERTEX: "真实云模型运行", UNKNOWN_SAFE: "策略未验证" },
      mappingSources: { STRUCTURED: "结构化材料编译，不调用映射模型", INCOMPLETE: "已有映射证据不完整，来源暂不可核对", FROZEN: "封存映射回执", LOCAL: "本地映射", LIVE: "新的 Vertex 映射", UNAVAILABLE: "本地模式无可用的新映射", OTHER: "后端策略声明的映射" },
      callBoundary: { none: "不发起新的外部模型调用。", mapping: "候选映射会发起外部模型调用；影子运行不会。", shadow: "当前候选映射回执会直接复用；只有影子运行会发起新的外部模型调用。", both: "候选映射和影子运行都会发起外部模型调用；已配置不等于运行成功。" },
      blockedByPolicy: { FROZEN_REPLAY: "冻结证据回放只允许验证后端已提供的回执。", OFFLINE_LOCAL: "本地交互演示未开放该模型动作。", LIVE_VERTEX: "真实云模型策略未开放该动作；请检查服务端配置。", UNKNOWN_SAFE: "后端未提供可验证的执行策略；为安全起见已禁用此动作。" },
      requestFailed: "适配请求失败",
      deploymentError: "准入依赖不可用。请管理员核对安装包中的验证资源与部署配置，修复后刷新并重新审阅；操作不会自动重发。（{code}）",
      apiError: "请求未确认。请刷新适配状态核对结果；操作不会自动重发。（{code}）",
      staleRequest: "候选或审批依据已变化。请刷新适配状态，按最新内容重新审阅。（{code}）",
      noDigest: "尚未生成摘要",
      noReason: "无稳定原因码",
      targetPath: "目标",
      unknownObjective: "受治理企业报价 · 零外部效应",
      governedQuoteObjective: "形成一份零外部效应的受治理企业报价",
      domainNames: { product: "产品", legal: "法务", finance: "财务", gtm: "市场与商业化" },
      contractsBound: "{count}/{count} 个领域契约已绑定",
      projectionsBound: "{count}/{count} 份最小投影已绑定",
      enhancedTitle: "增强验收（可选）",
      enhancedDetail: "上下文编译与影子运行用于加强信心，不是企业接入的必经步骤",
      enhancedWaiting: "等待契约激活",
      reviewKicker: "负责人审阅对象",
      reviewTitle: "将企业材料编译为可准入的组织契约",
      reviewWaiting: "完整候选与审阅摘要尚不可用；准入前必须审阅业务语义和效应边界。",
      reviewReady: "以下内容由服务端从已准入材料确定性生成，并与本次批准精确绑定。",
      reviewApproved: "负责人已批准这一版业务摘要；后续业务只能消费这个精确版本。",
      reviewLineageInvalid: "已激活状态缺少完整的负责人审阅链，不能继续使用。",
      sourceMaturityPending: "等待识别材料性质",
      sourceMaturity: {
        CONTROLLED_SYNTHETIC_ENTERPRISE_INPUT: "受控合成企业样例 · 可操作验证",
        PUBLIC_DATA_INPUT: "公开数据 · 需核对业务适用性",
        ENTERPRISE_INTERNAL_DECLARED_INPUT: "企业内部材料 · 内容由企业声明",
        ENTERPRISE_CONFIDENTIAL_DECLARED_INPUT: "企业机密材料 · 内容由企业声明",
      },
      organizationLabel: "组织与任务",
      domainLabel: "领域范围",
      factLabel: "事实与权威",
      dependencyLabel: "依赖与变更",
      domainsCount: "{count} 个领域",
      factsCount: "{count} 条已准入事实",
      authoritiesCount: "{count} 个权威来源",
      dependenciesCount: "{targets} 个影响目标 · {edges} 条依赖边",
      changesCount: "{count} 类变更",
      reviewComponentAria: "五类企业材料如何形成契约",
      observedItems: "识别 {count} 项",
      componentWaiting: "等待映射",
      componentComplete: "已映射",
      componentBlocked: "有未知项",
      responsibleFor: "负责：{owners}",
      componentEffects: {
        DEFINE_TASK_AND_DOMAIN_SCOPE: "确定业务任务与领域范围",
        BIND_EVIDENCE_OBLIGATIONS_AND_SAFE_FACTS: "绑定可使用的事实与证据义务",
        BIND_ACTORS_OWNERS_AND_APPROVAL_BOUNDARIES: "绑定参与者、负责人和审批边界",
        BIND_TEMPLATE_AND_DOMAIN_CAPABILITY_SCOPE: "识别模板与能力范围，供 OrgRebase 运行时投影使用",
        BIND_CHANGE_IMPACT_AND_CONTEXT_HANDOFF_SCOPE: "识别依赖与变更范围，供 OrgRebase 影响计算使用",
        BIND_PORTABLE_ORGANIZATIONAL_DEPENDENCY_AND_HANDOFF_SCOPE: "绑定可迁移的组织依赖与交接范围",
      },
      runtimeProjectionBoundary: "OAC 把来源、角色与主体、需求、能力范围、依赖投影和准入结果编译为任务形成根。当前业务运行只有在激活绑定、组队决策、最小上下文与 AgentTeams 执行计划精确一致时才会启动；OrgRebase 仍是影响判断、人工批准与规范写入的唯一控制面。",
      unknownsLabel: "待人判断的未知项",
      unknownsWaiting: "等待候选映射",
      unknownsClear: "未发现阻断性未知项",
      unknownsCount: "{count} 个未知项已明确保留",
      unknownsClearDetail: "如后续材料发生变化，必须重新生成候选并审批。",
      effectsLabel: "批准后会发生",
      nonEffectsLabel: "批准后不会发生",
      effects: {
        ACTIVATE_EXACT_ORGANIZATION_CONTRACT: "激活这一精确版本的组织契约",
        ALLOW_EXACT_WORKSPACE_FORMATION_GATE: "放行当前工作区的任务形成门",
      },
      nonEffects: {
        NO_QUOTE_BUSINESS_APPROVAL: "不会批准任何报价业务结果",
        NO_EXTERNAL_SYSTEM_WRITE: "不会写入企业外部系统",
        NO_AGENT_AUTHORITY_EXPANSION: "不会扩大智能体权限",
      },
      acknowledgementsTitle: "准入前请逐项确认",
      acknowledgementsRecordedTitle: "本次准入确认记录",
      acknowledgementLabels: ["我已审阅企业材料到组织契约的对应关系", "我已了解并接受当前保留的未知项", "我了解本次准入不等于报价审批，也不会写入外部系统"],
      acknowledgementsNote: "三项确认完成且达到服务端最短审阅时间后，才能准入。",
      acknowledgementsRecordedNote: "三项确认与服务端 4 秒时间门均已记录，并与本次激活精确绑定。",
      acknowledgementMissing: "请先阅读并勾选三项准入确认",
      summaryMissing: "服务端尚未绑定可审阅摘要，不能准入",
      businessTokens: {
        "org:evergreen-industries": "常青工业",
        "customer:blue-harbor": "蓝港客户",
        "employee:enterprise-quote-operator": "报价运营负责人",
        enterprise_quote: "企业报价",
        governed_quote_v1: "协作工作稿",
        QUOTE: "报价交付物",
        product_plan: "产品方案",
        launch_date: "发布日期",
        data_residency: "数据驻留范围",
        notice_required: "是否需要客户通知",
        price_band: "定价级别",
        currency: "结算币种",
        partner_terms: "合作条款状态",
        quote_compose_skill: "报价编排 Skill",
        public_message: "对外说明",
        "enterprise-plan-v4": "企业方案当前版",
        strategic: "战略客户价位",
        "legal-review": "待法务审阅",
        "template:enterprise_quote": "企业报价模板",
        "capability-card:product": "产品领域能力卡",
        "capability-card:legal": "法务领域能力卡",
        "capability-card:finance": "财务领域能力卡",
        "capability-card:gtm": "市场与商业化领域能力卡",
        "skill:enterprise-quote-compose": "企业报价编排 Skill",
        "skill:enterprise-launch-readiness": "企业发布就绪 Skill",
        "work:quote_acme": "企业报价工作项",
        "work:finance_analysis_d": "财务分析工作项",
        "work:partner_brief_e": "合作伙伴简报工作项",
        "work:finance-approval": "财务审批工作项",
        "work:launch-readiness-review": "上线就绪审查工作项",
        "work:partner-brief": "合作伙伴简报工作项",
        "employee:sales-owner": "报价运营负责人",
        "human:evergreen-workspace-owner": "常青工业工作区负责人",
        "human:evergreen-product-owner": "常青工业产品负责人",
        "human:evergreen-finance-owner": "常青工业财务负责人",
        "human:evergreen-legal-owner": "常青工业法务负责人",
        "human:evergreen-gtm-owner": "常青工业市场与商业化负责人",
        "human:product-owner": "产品负责人",
        "human:finance-owner": "财务负责人",
        "human:workspace-runtime-owner": "工作区运行负责人",
        "authority:product": "产品权威源",
        "authority:legal": "法务权威源",
        "authority:finance": "财务权威源",
        "authority:gtm": "市场与商业化权威源",
        "authority:skill-registry": "Skill 注册表权威源",
        "authority:evergreen-product": "常青工业产品权威源",
        "authority:evergreen-finance": "常青工业财务权威源",
        "authority:evergreen-legal": "常青工业法务权威源",
        "authority:evergreen-gtm": "常青工业市场与商业化权威源",
        true: "是",
        false: "否",
      },
      sampleKeys: { task: "任务", task_type: "任务类型", domain: "领域", field: "业务字段", slot_id: "事实槽位", role: "角色", actor: "执行人", owner: "负责人", target: "影响对象", template: "任务模板" },
      sensitivityLabels: { PUBLIC: "公开", INTERNAL: "内部", CONFIDENTIAL: "机密" },
      restrictedValue: "字段已识别，具体值请在受限视图中查看",
      hiddenLocalPath: "本地路径已隐藏",
    }),
    en: Object.freeze({
      kicker: "ENTERPRISE ONBOARDING · ORGANIZATIONAL CONTRACT ADAPTATION",
      title: "Let an Intake Agent compile enterprise material into a verifiable OAC workspace",
      introLabel: "OAC ENTERPRISE ADAPTATION LAYER",
      reviewJump: "View review and actions",
      introTitle: "The agent lowers adaptation cost; contracts and a named human retain final authority",
      introBodyByMode: {
        FROZEN_REPLAY: "OAC defines the inputs, authority, handoff, and admission rules that agents must follow. The Intake Agent only proposes a candidate; deterministic validation and the named owner decide whether it may proceed. This mode only replays and revalidates sealed receipts, without a new model call. Material maturity comes from the backend review summary.",
        OFFLINE_LOCAL: "OAC defines the inputs, authority, handoff, and admission rules that agents must follow. The Intake Agent only proposes a candidate; deterministic validation and the named owner decide whether it may proceed. This mode exposes only local actions explicitly allowed by backend policy. Material maturity comes from the backend review summary.",
        LIVE_VERTEX: "OAC defines the inputs, authority, handoff, and admission rules that agents must follow. The Intake Agent only proposes a candidate; deterministic validation and the named owner decide whether it may proceed. A new Vertex mapping or enhanced-assurance run occurs only when backend policy explicitly allows it. Material maturity comes from the backend review summary.",
        UNKNOWN_SAFE: "OAC defines the inputs, authority, handoff, and admission rules that agents must follow. The Intake Agent only proposes a candidate. The backend did not provide a verifiable execution policy or workspace gate, so this view fails closed.",
      },
      steps: ["Enterprise material", "Candidate mapping", "OAC contract validation", "Owner review and admission", "Contract activation", "Business consumption"],
      stepDetails: ["Domain · Knowledge · Authority · Capability · Dependency", "Intake Agent proposes only", "Source · Demand · Authority · Unknowns", "Business summary · named owner", "Exact binding to this Workspace", "Daily task references the activated contract"],
      sourceNames: { DOMAIN: "Domain", KNOWLEDGE: "Knowledge", AUTHORITY: "Authority", CAPABILITY: "Capability", DEPENDENCY: "Dependency" },
      sourceCandidate: "candidate mapping",
      sourceGridLabel: "Technical bindings for five enterprise input classes",
      sourceWaiting: "awaiting mapping",
      sourceComplete: "mapped",
      sourceBlocked: "gap retained",
      statusLoading: "Reading adaptation state",
      statusUnavailable: "Adaptation service is not ready",
      status: {
        NOT_STARTED: "Enterprise material is ready",
        AGENT_MAPPING_HOLD: "The Intake Agent retained unresolved gaps",
        OWNER_REVIEW_PENDING: "Awaiting Enterprise Contract Owner admission",
        READY_FOR_SHADOW: "Contract admitted and activated; the governed evolution Workspace is ready",
        SHADOW_COMPLETED: "Contract activated; enhanced assurance completed",
      },
      runLabel: "Adaptation run",
      runDetailSeparate: "Separate adaptation task; does not affect the active business run",
      runDetailRequiredPending: "Required precondition gate; awaiting an exact activation binding",
      runDetailBound: "Exactly bound to the current Workspace run",
      runDetailConsumed: "Consumed by this quote-formation transaction",
      contractLabel: "OAC contract binding",
      contractDetail: "Source / Demand / Admission digests · review gate {gate} · human wait {wait} · approval {approval}",
      gapLabel: "Gaps and reasons",
      gapChecking: "Checking",
      gapUnavailable: "Gap details are incomplete",
      gapNone: "0 gaps",
      gapCount: "{count} gaps",
      productGapLabel: "ENTERPRISE FACT GAPS",
      productGapClear: "All required facts are mapped",
      productGapBlocked: "The system stopped and is waiting for missing facts",
      productGapWaiting: "Candidate mapping has not completed",
      productAdmissionLabel: "HUMAN ADMISSION",
      productAdmissionComplete: "Contract and human admission complete",
      productAdmissionPending: "Awaiting the named human owner",
      productAdmissionBlocked: "GAPS REMAIN · ADMISSION BLOCKED",
      productAdmissionWaiting: "Awaiting contract validation",
      productAdmissionDetail: "Only the named human owner may approve",
      productValidationLabel: "CONTRACT VALIDATION",
      productValidationWaiting: "AWAITING CANDIDATE",
      productValidationUnknown: "VALIDATION RESULT INCOMPLETE",
      productValidationPass: "FIVE MATERIAL CLASSES PASSED DETERMINISTIC VALIDATION",
      productValidationHold: "VALIDATION RETAINED UNKNOWNS",
      productValidationDetail: "Sources, authority, and demand must match exactly",
      productActivationLabel: "CONTRACT ACTIVATION",
      productActivationWaiting: "AWAITING HUMAN ADMISSION",
      productActivationReady: "CONTRACT ACTIVATED",
      productActivationConsumed: "CONSUMED BY THE CURRENT BUSINESS TASK",
      productActivationDetail: "Activation is required before entering the governed evolution Workspace",
      productShadowLabel: "SHADOW EXECUTION",
      productShadowReady: "ADMITTED · AWAITING SHADOW EXECUTION",
      productShadowNotRun: "AWAITING OWNER ADMISSION",
      productShadowDetail: "Validates the adaptation without changing formal state",
      productEffectLabel: "CURRENT EFFECT BOUNDARY",
      productOutcomesLabel: "OAC enterprise-adaptation outcomes",
      productBusinessLabel: "BUSINESS START GATE",
      businessBlocked: "QUOTE NOT RELEASED",
      businessReady: "QUOTE MAY BE FORMED",
      businessConsumed: "THIS QUOTE CONSUMED OAC",
      businessOptional: "OAC IS AN OPTIONAL VALIDATION ADD-ON",
      businessBlockedDetail: "The organizational contract is not admitted; the server will not form an internal working draft",
      businessReadyDetail: "The activation binding locks this Workspace run; an internal working draft may now be formed",
      businessConsumedDetail: "The collaborative working-draft transaction persisted the activation binding and consumption receipt",
      businessConsumedOptionalDetail: "This quote precisely bound and consumed OAC; the current deployment policy does not require every task to use OAC",
      businessOptionalDetail: "This Workspace does not require OAC admission; the quote flow remains available",
      businessUnknownDetail: "The business-start gate is incomplete; this view fails closed to prevent a contract bypass",
      businessLineageInvalid: "CONTRACT EVIDENCE INCOMPLETE · USE STOPPED",
      businessLineageInvalidDetail: "The state claims activation, but the owner summary, approval, or activation binding is incomplete; business consumption is blocked",
      causalInputs: "{count} enterprise input types",
      causalDomains: "{count} domains",
      causalContext: "context bound",
      causalHuman: "human gate passed",
      causalTool: "Tool in evidence chain",
      causalSkill: "Skill in evidence chain",
      causalWaiting: "Causal evidence appears only as the real execution progresses",
      auditTitle: "Advanced audit evidence",
      auditDetail: "Digests, internal paths, runtime bindings, and technical receipts; not required for routine review",
      effectLabel: "Effect ceiling",
      effectZero: "Zero external effects",
      effectDetail: "Canonical writes {writes} · candidate is not canonical state",
      effectDeclared: "Declared ceiling: external writes prohibited",
      effectDeclaredDetail: "Not executed yet. This shows the contract limits; actual writes are checked after execution.",
      agentLabel: "Intake Agent",
      agentNotRun: "Not run",
      agentVerified: "{suggestion} verified",
      agentHold: "Failed closed",
      agentDetail: "{runtime} · {actions} native actions · {model}",
      productAgentWaiting: "The real model receipt and native AgentTeams actions appear after a candidate is formed",
      structuredLabel: "Candidate preparation",
      structuredReady: "Candidate compiled from configured materials",
      structuredWaiting: "Awaiting compilation of configured materials",
      structuredDetail: "Deterministic compilation with no mapping-model call; contract checks and designated-owner admission still apply.",
      providerVertexLive: "Live Vertex model advisory",
      providerLocal: "Local model advisory",
      providerStructured: "Structured materials · deterministic compilation",
      providerIncomplete: "Mapping evidence incomplete",
      mappingRateLimited: "Model service unavailable (rate limited)",
      mappingServiceFailed: "Model service request did not succeed",
      mappingSchemaFailed: "Model candidate failed structural checks",
      mappingNoCandidate: "No admissible candidate was produced in this attempt",
      mappingNotAssessed: "Enterprise fact gaps have not been assessed",
      mappingFailureRecovery: "Wait for service recovery or investigate project quota, then ask an administrator to preserve the failed record and retry in a new workspace. Refresh only reads existing records; it does not resend the request.",
      providerReceipt: "Model advisory receipt",
      modelStatuses: { VALID: "VALID", NOT_RUN: "AWAITING MODEL CANDIDATE", HOLD: "HOLD", ABSTAIN: "ABSTAIN", PROVIDER_ERROR: "PROVIDER ERROR", SCHEMA_ERROR: "SCHEMA ERROR" },
      bindingLabel: "Run binding",
      bindingNone: "Not bound yet",
      bindingDetail: "Adaptation run → adapter capsule → execution run",
      bindingReady: "Capsule admitted · awaiting shadow execution",
      bindingConsumed: "activation binding {activation} · consumption receipt {consumption}",
      independentVerified: "Independent verification: {outputs} outputs / {packs} pack files / 0 failures",
      layerNames: { SOURCE: "Source", CONTEXT: "Context", AGENTTEAMS: "AgentTeams", TOOL: "Tool", SKILL: "Skill", OTLP: "Telemetry", CANDIDATE: "Candidate" },
      parityDetail: "{obligations} stable obligations → {attempts} dynamic attempts · Finance ABSTAIN→retry · Reviewer REPLAN→PASS",
      ownerLabel: "Human authority",
      ownerEvergreen: "Evergreen Workspace Owner",
      ownerWorkspace: "Enterprise Workspace Owner",
      controlNote: "The countdown is a review aid only. The server-enforced minimum review gate, three confirmations, and exact summary jointly authorize admission.",
      refresh: "Refresh adaptation state",
      refreshing: "Reading…",
      prepare: "Prepare candidate adaptation",
      prepareReplay: "Validate onboarding candidate",
      prepareOffline: "Run local candidate adaptation",
      prepareStructured: "Generate contract candidates from configured materials",
      prepareLive: "Run a new Vertex candidate mapping",
      preparing: "Preparing candidate adaptation…",
      review: "Review adaptation · {seconds}s",
      approve: "Approve and activate organization contract",
      approving: "Binding human admission…",
      executeShadow: "Execute OAC-bound shadow run",
      shadowReplay: "Replay and verify shadow-execution receipt",
      shadowOffline: "Run local shadow validation",
      shadowLive: "Run a new Vertex shadow validation",
      continueToQuote: "Continue to Quote workspace",
      shadowOptional: "Optional shadow replay · business gate is open",
      actionDisabled: "This action is unavailable in the current mode",
      executingShadow: "Executing shadow run…",
      runIndependentVerification: "Run independent verification",
      runningIndependentVerification: "Running independent verification…",
      shadowCompletedPending: "Shadow run completed · independent verification pending",
      shadowVerified: "Shadow run and independent verification passed",
      held: "Gaps remain · admission blocked",
      rejected: "Rejected · run blocked",
      contextKicker: "WHERE CONTEXT RESIDES",
      contextTitle: "The task coordinator holds run-level summaries; domain agents receive only what this task requires",
      contextWaiting: "Compiled after human admission",
      contextUnanchored: "This historical context has no verifiable source record and cannot be used for execution.",
      contextCompiled: "Context is exactly bound",
      managerTitle: "Deterministic task coordinator · Manager role",
      managerDetail: "objective: {objective} · {obligations} evidence obligations · minimum coalition",
      managerFormationBound: "formation-decision receipt bound",
      managerContextBound: "organizational intent and formation decision bound",
      topologyExact: "Formation selected {planned} domains · AgentTeams created {actual} · exact match",
      topologyBound: "Formation decision bound; use the active-run receipt for the actual AgentTeams topology",
      topologyPending: "Awaiting the Formation plan to compile into actual AgentTeams tasks",
      residentTitle: "Resident domain contracts",
      residentDetail: "{count} domains · authority source types · allowed Tool / Skill · abstention rules",
      taskTitle: "Task-specific projections",
      taskDetail: "{count} minimum projections · purpose · recipient · expiry · revision lock",
      returnTitle: "Candidate return",
      returnDetail: "candidate result → Reviewer Agent → deterministic control plane",
      writesZero: "canonical writes 0",
      policyBoundary: "Current mode: {mode} · candidate source: {source}. Next-action external-call boundary: {calls} Only an observed model receipt is presented as a real run. The onboarding Agent proposes only; OAC validation and the designated owner decide whether to activate it.",
      policyModes: { FROZEN_REPLAY: "Frozen evidence replay", OFFLINE_LOCAL: "Local interactive demo", LIVE_VERTEX: "Live cloud-model execution", UNKNOWN_SAFE: "Policy not verified" },
      mappingSources: { STRUCTURED: "structured-material compilation, with no mapping-model call", INCOMPLETE: "retained mapping evidence is incomplete; its source cannot yet be verified", FROZEN: "sealed mapping receipt", LOCAL: "local mapping", LIVE: "new Vertex mapping", UNAVAILABLE: "no new mapping available in local mode", OTHER: "backend-declared mapping" },
      callBoundary: { none: "No new external-model call will be made.", mapping: "Candidate mapping will call an external model; shadow execution will not.", shadow: "The current candidate-mapping receipt will be reused; only shadow execution will make a new external-model call.", both: "Candidate mapping and shadow execution will call an external model; configuration alone is not a successful run." },
      blockedByPolicy: { FROZEN_REPLAY: "Frozen-evidence replay only verifies receipts already supplied by the backend.", OFFLINE_LOCAL: "The local interactive demo does not expose this model action.", LIVE_VERTEX: "Live cloud-model policy has not enabled this action; check the server configuration.", UNKNOWN_SAFE: "The backend did not provide a verifiable execution policy, so this action is disabled for safety." },
      requestFailed: "Adaptation request failed",
      deploymentError: "Admission dependencies are unavailable. Ask an administrator to check the installation verification resources and deployment configuration, then refresh and review again after repair. No operation is replayed automatically. ({code})",
      apiError: "Request unconfirmed. Refresh the adaptation state to inspect the result; no operation is replayed automatically. ({code})",
      staleRequest: "The candidate or approval basis changed. Refresh the adaptation state and review the latest content. ({code})",
      noDigest: "Digest not produced",
      noReason: "No stable reason code",
      targetPath: "target",
      unknownObjective: "governed enterprise quote · zero external effects",
      governedQuoteObjective: "produce one governed enterprise quote with zero external effects",
      domainNames: { product: "Product", legal: "Legal", finance: "Finance", gtm: "GTM" },
      contractsBound: "{count}/{count} domain contracts bound",
      projectionsBound: "{count}/{count} minimum projections bound",
      enhancedTitle: "Enhanced assurance (optional)",
      enhancedDetail: "Context compilation and shadow execution increase confidence; they are not onboarding steps",
      enhancedWaiting: "Awaiting contract activation",
      reviewKicker: "OWNER REVIEW SUBJECT",
      reviewTitle: "Compile enterprise material into an admissible organizational contract",
      reviewWaiting: "The complete candidate and review summary are unavailable. Business semantics and effect boundaries must be reviewed before admission.",
      reviewReady: "The server deterministically derived this view from the admitted material and binds it exactly to this approval.",
      reviewApproved: "The owner approved this exact business summary; later business work may consume only this version.",
      reviewLineageInvalid: "The activated state lacks a complete owner-review lineage and cannot be used.",
      sourceMaturityPending: "Awaiting material-maturity classification",
      sourceMaturity: {
        CONTROLLED_SYNTHETIC_ENTERPRISE_INPUT: "CONTROLLED SYNTHETIC ENTERPRISE EXAMPLE · OPERABLE VALIDATION",
        PUBLIC_DATA_INPUT: "PUBLIC DATA · BUSINESS FIT TO VERIFY",
        ENTERPRISE_INTERNAL_DECLARED_INPUT: "ENTERPRISE INTERNAL · DECLARED BY OWNER",
        ENTERPRISE_CONFIDENTIAL_DECLARED_INPUT: "ENTERPRISE CONFIDENTIAL · DECLARED BY OWNER",
      },
      organizationLabel: "ORGANIZATION AND TASK",
      domainLabel: "DOMAIN SCOPE",
      factLabel: "FACTS AND AUTHORITY",
      dependencyLabel: "DEPENDENCIES AND CHANGE",
      domainsCount: "{count} domains",
      factsCount: "{count} admitted facts",
      authoritiesCount: "{count} authority sources",
      dependenciesCount: "{targets} impact targets · {edges} dependency edges",
      changesCount: "{count} change types",
      reviewComponentAria: "How five enterprise material classes become a contract",
      observedItems: "{count} items observed",
      componentWaiting: "AWAITING MAPPING",
      componentComplete: "MAPPED",
      componentBlocked: "UNKNOWNS RETAINED",
      responsibleFor: "Responsible: {owners}",
      componentEffects: {
        DEFINE_TASK_AND_DOMAIN_SCOPE: "Define business-task and domain scope",
        BIND_EVIDENCE_OBLIGATIONS_AND_SAFE_FACTS: "Bind usable facts and evidence obligations",
        BIND_ACTORS_OWNERS_AND_APPROVAL_BOUNDARIES: "Bind actors, owners, and approval boundaries",
        BIND_TEMPLATE_AND_DOMAIN_CAPABILITY_SCOPE: "Identify template and capability scope for OrgRebase runtime projection",
        BIND_CHANGE_IMPACT_AND_CONTEXT_HANDOFF_SCOPE: "Identify dependency and change scope for OrgRebase impact computation",
        BIND_PORTABLE_ORGANIZATIONAL_DEPENDENCY_AND_HANDOFF_SCOPE: "Bind portable organizational dependency and handoff scope",
      },
      runtimeProjectionBoundary: "OAC compiles sources, roles and principals, demand, capability scope, dependency projections, and admission into task-formation roots. A business run starts only when the activation binding, formation decision, minimum context, and AgentTeams execution plan match exactly; OrgRebase remains the sole control plane for impact decisions, human approval, and canonical writes.",
      unknownsLabel: "UNKNOWNS REQUIRING HUMAN JUDGMENT",
      unknownsWaiting: "AWAITING CANDIDATE MAPPING",
      unknownsClear: "NO BLOCKING UNKNOWNS OBSERVED",
      unknownsCount: "{count} unknowns explicitly retained",
      unknownsClearDetail: "If the source material changes, the system must create and approve a new candidate.",
      effectsLabel: "WHAT APPROVAL WILL DO",
      nonEffectsLabel: "WHAT APPROVAL WILL NOT DO",
      effects: {
        ACTIVATE_EXACT_ORGANIZATION_CONTRACT: "Activate this exact organizational-contract version",
        ALLOW_EXACT_WORKSPACE_FORMATION_GATE: "Release the formation gate for this Workspace",
      },
      nonEffects: {
        NO_QUOTE_BUSINESS_APPROVAL: "It will not approve any quote business result",
        NO_EXTERNAL_SYSTEM_WRITE: "It will not write to an external enterprise system",
        NO_AGENT_AUTHORITY_EXPANSION: "It will not expand Agent authority",
      },
      acknowledgementsTitle: "Confirm each item before admission",
      acknowledgementsRecordedTitle: "Admission confirmation record",
      acknowledgementLabels: ["I reviewed the enterprise-material to organizational-contract mapping", "I understand and accept the currently retained Unknowns", "I understand that admission is not quote approval and does not write to external systems"],
      acknowledgementsNote: "Admission requires all three confirmations and the server-enforced minimum review interval.",
      acknowledgementsRecordedNote: "All three confirmations and the server-enforced 4-second review gate are recorded and exactly bound to this activation.",
      acknowledgementMissing: "Review and check all three admission confirmations",
      summaryMissing: "The server has not bound a reviewable summary; admission is disabled",
      businessTokens: {
        "org:evergreen-industries": "Evergreen Industries",
        "customer:blue-harbor": "Blue Harbor customer",
        "employee:enterprise-quote-operator": "Quote Operations Owner",
        enterprise_quote: "Enterprise quote",
        governed_quote_v1: "Collaborative working draft",
        QUOTE: "Quote deliverable",
        product_plan: "Product plan",
        launch_date: "Launch date",
        data_residency: "Data-residency scope",
        notice_required: "Customer notice required",
        price_band: "Pricing tier",
        currency: "Settlement currency",
        partner_terms: "Partner-terms status",
        quote_compose_skill: "Quote-composition Skill",
        public_message: "Public message",
        "enterprise-plan-v4": "Current enterprise plan",
        strategic: "Strategic-customer tier",
        "legal-review": "Pending legal review",
        "template:enterprise_quote": "Enterprise-quote template",
        "capability-card:product": "Product domain capability card",
        "capability-card:legal": "Legal domain capability card",
        "capability-card:finance": "Finance domain capability card",
        "capability-card:gtm": "GTM domain capability card",
        "skill:enterprise-quote-compose": "Enterprise quote-composition Skill",
        "skill:enterprise-launch-readiness": "Enterprise launch-readiness Skill",
        "work:quote_acme": "Enterprise-quote work item",
        "work:finance_analysis_d": "Finance-analysis work item",
        "work:partner_brief_e": "Partner-brief work item",
        "work:finance-approval": "Finance approval work item",
        "work:launch-readiness-review": "Launch-readiness review work item",
        "work:partner-brief": "Partner-brief work item",
        "employee:sales-owner": "Quote Operations Owner",
        "human:evergreen-workspace-owner": "Evergreen Workspace Owner",
        "human:evergreen-product-owner": "Evergreen Product Owner",
        "human:evergreen-finance-owner": "Evergreen Finance Owner",
        "human:evergreen-legal-owner": "Evergreen Legal Owner",
        "human:evergreen-gtm-owner": "Evergreen GTM Owner",
        "human:product-owner": "Product Owner",
        "human:finance-owner": "Finance Owner",
        "human:workspace-runtime-owner": "Workspace Runtime Owner",
        "authority:product": "Product authority source",
        "authority:legal": "Legal authority source",
        "authority:finance": "Finance authority source",
        "authority:gtm": "GTM authority source",
        "authority:skill-registry": "Skill-registry authority source",
        "authority:evergreen-product": "Evergreen product authority source",
        "authority:evergreen-finance": "Evergreen finance authority source",
        "authority:evergreen-legal": "Evergreen legal authority source",
        "authority:evergreen-gtm": "Evergreen GTM authority source",
        true: "Yes",
        false: "No",
      },
      sampleKeys: { task: "Task", task_type: "Task type", domain: "Domain", field: "Business field", slot_id: "Fact slot", role: "Role", actor: "Actor", owner: "Owner", target: "Impact target", template: "Task template" },
      sensitivityLabels: { PUBLIC: "Public", INTERNAL: "Internal", CONFIDENTIAL: "Confidential" },
      restrictedValue: "Field identified; inspect its value in the restricted view",
      hiddenLocalPath: "Local path hidden",
    }),
  });

  let currentLanguage = document.documentElement.lang === "en" ? "en" : "zh-CN";
  let currentAdaptation = null;
  let renderedReviewSubject = null;
  let requestInFlight = false;
  let refreshRequest = null;
  let refreshVersion = 0;
  let refreshAfterAction = false;
  let activeAction = null;
  let countdownTimer = null;
  let serviceError = null;
  let actionError = null;
  let commandVersion = 0;
  const sessionIdentity = session => session ? JSON.stringify([session.mode, session.authenticated,
    session.principal?.tenant_id, session.principal?.actor_id,
    session.principal?.issuer, session.principal?.subject]) : null;
  let currentSessionIdentity = sessionIdentity(window.OrgRebaseClient.session());

  const byId = (id) => document.getElementById(id);
  const copy = () => COPY[currentLanguage];
  const setText = (id, value) => {
    const node = byId(id);
    if (node) node.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
  };
  const setTitle = (id, ...values) => {
    const node = byId(id);
    if (!node) return;
    const exact = values.filter((value) => value !== undefined && value !== null && value !== "").join(" · ");
    if (exact) node.title = exact;
    else node.removeAttribute("title");
  };
  const format = (template, values = {}) => String(template).replace(/\{(\w+)\}/g, (_, key) => values[key] ?? `{${key}}`);
  const compact = (value, width = 22) => {
    if (!value) return "—";
    const raw = String(value);
    return raw.length > width ? `${raw.slice(0, width)}…` : raw;
  };
  const first = (...values) => values.find((value) => value !== undefined && value !== null && value !== "");
  const arrayOf = (value) => Array.isArray(value) ? value : [];

  function policyActionToken(value) {
    return String(value || "").trim().toUpperCase().replace(/[\s-]+/g, "_");
  }

  function normalizeBlockedReasons(value) {
    if (value && !Array.isArray(value) && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([action, reason]) => [
        policyActionToken(action),
        Array.isArray(reason)
          ? reason.map((item) => typeof item === "string" ? item : first(item && item.reason_code, item && item.code, item && item.message)).filter(Boolean).join(" · ")
          : typeof reason === "string"
            ? reason
            : first(reason && reason.reason_code, reason && reason.code, reason && reason.message),
      ]));
    }
    const normalized = {};
    arrayOf(value).forEach((item) => {
      if (typeof item === "string") {
        const upper = item.toUpperCase();
        const action = Object.values(POLICY_ACTIONS).find((candidate) => upper.includes(candidate))
          || (upper.includes("MAPPING") || upper.includes("AGENT_PREPARE") ? POLICY_ACTIONS.prepare : null)
          || (upper.includes("SHADOW") ? POLICY_ACTIONS.shadow : null);
        if (action) normalized[action] = item;
        return;
      }
      if (!item || typeof item !== "object") return;
      const action = policyActionToken(first(item.action, item.action_id, item.name));
      if (!action) return;
      normalized[action] = first(item.reason_code, item.code, item.reason, item.message);
    });
    return normalized;
  }

  function normalizeExecutionPolicy(composite, raw, agentMapping) {
    const source = composite.execution_policy || raw.execution_policy || {};
    const declaredMode = policyActionToken(source.mode);
    const mode = POLICY_MODES.includes(declaredMode) ? declaredMode : "UNKNOWN_SAFE";
    const availableActions = arrayOf(source.available_actions).map(policyActionToken).filter(Boolean);
    return {
      present: POLICY_MODES.includes(declaredMode),
      mode,
      mappingSource: first(source.mapping_source, "UNSPECIFIED"),
      mappingModelProvider: first(source.mapping_model_provider, agentMapping.model_provider, agentMapping.provider, "UNSPECIFIED"),
      modelProvider: first(source.model_provider, source.mapping_model_provider, agentMapping.model_provider, agentMapping.provider, "UNSPECIFIED"),
      mappingWillCallExternalModel: first(
        source.mapping_will_call_external_model,
        source.mapping_will_invoke_external_model,
        false,
      ) === true,
      shadowWillCallExternalModel: first(
        source.shadow_will_call_external_model,
        source.shadow_will_invoke_external_model,
        false,
      ) === true,
      availableActions,
      blockedReasons: normalizeBlockedReasons(source.blocked_reasons),
    };
  }

  function closedWorkspaceGate(source = {}) {
    return {
      valid: false,
      mode: "unknown",
      requiresOacAdmission: true,
      formAllowed: false,
      status: "BLOCKED_PENDING_OAC",
      executionRunId: first(source.execution_run_id),
      activationBindingDigest: first(source.activation_binding_digest),
      consumptionReceiptDigest: first(source.consumption_receipt_digest),
    };
  }

  function normalizeWorkspaceGate(composite, raw) {
    const source = composite.workspace_gate || raw.workspace_gate;
    if (!source || typeof source !== "object" || Array.isArray(source)) return closedWorkspaceGate();
    const mode = String(source.mode || "").trim().toLowerCase();
    const status = String(source.status || "").trim().toUpperCase();
    const requiresOacAdmission = source.requires_oac_admission;
    const formAllowed = source.form_allowed;
    const executionRunId = first(source.execution_run_id);
    const activationBindingDigest = first(source.activation_binding_digest);
    const consumptionReceiptDigest = first(source.consumption_receipt_digest);
    const commonValid = ["required", "optional"].includes(mode)
      && WORKSPACE_GATE_STATUSES.includes(status)
      && typeof requiresOacAdmission === "boolean"
      && typeof formAllowed === "boolean"
      && Boolean(executionRunId);
    const requiredValid = mode !== "required" || (
      requiresOacAdmission === true
      && (
        (status === "BLOCKED_PENDING_OAC" && formAllowed === false)
        || (status === "READY_TO_FORM" && formAllowed === true && Boolean(activationBindingDigest))
        || (
          status === "CONSUMED_BY_QUOTE_FORMATION"
          && Boolean(activationBindingDigest)
          && Boolean(consumptionReceiptDigest)
        )
      )
    );
    const optionalValid = mode !== "optional" || (
      requiresOacAdmission === false
      && formAllowed === true
      && status !== "BLOCKED_PENDING_OAC"
    );
    if (!commonValid || !requiredValid || !optionalValid) return closedWorkspaceGate(source);
    return {
      valid: true,
      mode,
      requiresOacAdmission,
      formAllowed,
      status,
      executionRunId,
      activationBindingDigest,
      consumptionReceiptDigest,
    };
  }

  function policyAllows(state, action) {
    return Boolean(state && state.executionPolicy && state.executionPolicy.availableActions.includes(action));
  }

  function modelSuggestionLabel(state) {
    const c = copy();
    const policy = state.executionPolicy || {};
    if (policy.mappingSource === "INCOMPLETE_MAPPING_EVIDENCE") return c.providerIncomplete;
    if (policy.mappingSource === "STRUCTURED_MATERIALS") return c.providerStructured;
    if (policy.mode === "FROZEN_REPLAY") return c.providerReceipt;
    if (policy.mode === "OFFLINE_LOCAL") return c.providerLocal;
    const liveProvider = String(policy.mappingModelProvider || policy.modelProvider || "").toLowerCase();
    if (policy.mode === "LIVE_VERTEX" && (liveProvider.includes("vertex") || liveProvider.includes("gemini"))) {
      return c.providerVertexLive;
    }
    return c.providerReceipt;
  }

  function mappingSourceLabel(state) {
    const c = copy();
    const source = String(state.executionPolicy.mappingSource || "").toUpperCase();
    if (source === "INCOMPLETE_MAPPING_EVIDENCE") return c.mappingSources.INCOMPLETE;
    if (source === "STRUCTURED_MATERIALS") return c.mappingSources.STRUCTURED;
    if (source === "UNAVAILABLE_OFFLINE_LOCAL") return c.mappingSources.UNAVAILABLE;
    if (state.executionPolicy.mode === "FROZEN_REPLAY") return c.mappingSources.FROZEN;
    if (source.includes("VERTEX") || source.includes("LIVE")) return c.mappingSources.LIVE;
    if (source.includes("FROZEN") || source.includes("REPLAY")) return c.mappingSources.FROZEN;
    if (source.includes("LOCAL") || source.includes("OLLAMA")) return c.mappingSources.LOCAL;
    return c.mappingSources.OTHER;
  }

  function externalCallBoundary(state) {
    const c = copy();
    const policy = state.executionPolicy;
    if (policy.mappingWillCallExternalModel && policy.shadowWillCallExternalModel) return c.callBoundary.both;
    if (policy.mappingWillCallExternalModel) return c.callBoundary.mapping;
    if (policy.shadowWillCallExternalModel) return c.callBoundary.shadow;
    return c.callBoundary.none;
  }

  function policyActionLabel(state, action) {
    const c = copy();
    if (action === POLICY_ACTIONS.structured) return c.prepareStructured;
    if (action === POLICY_ACTIONS.prepare) {
      const mappingSource = String(state.executionPolicy.mappingSource || "").toUpperCase();
      if (state.executionPolicy.mode === "FROZEN_REPLAY" || mappingSource.includes("RECEIPT")) return c.prepareReplay;
      if (state.executionPolicy.mode === "OFFLINE_LOCAL") return c.prepareOffline;
      if (state.executionPolicy.mode === "LIVE_VERTEX") return c.prepareLive;
      return c.prepare;
    }
    if (state.executionPolicy.mode === "FROZEN_REPLAY" || state.status === "SHADOW_COMPLETED") return c.shadowReplay;
    if (state.executionPolicy.mode === "OFFLINE_LOCAL") return c.shadowOffline;
    if (state.executionPolicy.mode === "LIVE_VERTEX") return c.shadowLive;
    return c.executeShadow;
  }

  function policyBlockReason(state, action) {
    const c = copy();
    const policy = state && state.executionPolicy
      ? state.executionPolicy
      : { mode: "UNKNOWN_SAFE", blockedReasons: {} };
    const stableReason = policy.blockedReasons[action];
    if (typeof stableReason === "string"
      && stableReason.split(" · ").includes("OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_UNANCHORED")) {
      return { naturalReason: c.contextUnanchored, stableReason };
    }
    const naturalReason = c.blockedByPolicy[policy.mode] || c.blockedByPolicy.UNKNOWN_SAFE;
    return { naturalReason, stableReason };
  }

  function policyActionForStatus(state) {
    if (!state) return POLICY_ACTIONS.prepare;
    if (state.status === "NOT_STARTED") {
      return policyAllows(state, POLICY_ACTIONS.structured) ? POLICY_ACTIONS.structured : POLICY_ACTIONS.prepare;
    }
    return null;
  }

  function commandId(prefix) {
    const suffix = window.crypto && typeof window.crypto.randomUUID === "function"
      ? window.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${suffix}`;
  }

  function unwrap(payload) {
    if (!payload || typeof payload !== "object") return {};
    return payload.data || payload.result || payload;
  }

  function mappingList(raw, agentMapping, candidate) {
    const value = first(
      raw.candidate_mappings,
      agentMapping.accepted_mappings,
      raw.mappings,
      candidate.mappings,
      candidate.candidate_mappings,
    );
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object") {
      return Object.entries(value).map(([kind, mapping]) => ({ component_kind: kind, ...(mapping || {}) }));
    }
    return [];
  }

  function normalize(payload) {
    const composite = unwrap(payload);
    const raw = composite.adaptation || composite;
    const agentMapping = composite.agent_mapping || raw.agent_mapping || {};
    const contextResidency = composite.context_residency || raw.context_residency || {};
    const shadow = composite.shadow_execution || raw.shadow_execution || {};
    const candidate = raw.candidate || raw.prepared_candidate || raw.mapping_candidate || {};
    const approval = raw.approval || raw.admission || raw.source_admission || {};
    const reviewGate = raw.review_gate || approval.review_gate || {};
    const ownerReviewSummary = raw.owner_review_summary || candidate.owner_review_summary || {};
    const capsule = raw.adapter_capsule || raw.capsule || {};
    const lineageProof = raw.lineage_proof || {};
    const executionPolicy = normalizeExecutionPolicy(composite, raw, agentMapping);
    const status = String(first(composite.status, raw.status, "NOT_STARTED")).toUpperCase();
    const declaredGaps = arrayOf(first(raw.gaps, candidate.gaps, raw.declared_gaps));
    const modelFailed = ["PROVIDER_ERROR", "SCHEMA_ERROR"].includes(agentMapping.model_status);
    const gaps = status === "AGENT_MAPPING_HOLD" && !declaredGaps.length && !modelFailed
      ? arrayOf(agentMapping.validation_reason_codes) : declaredGaps;
    const mappings = mappingList(raw, agentMapping, candidate);
    const notBefore = first(raw.review_not_before, raw.not_before, reviewGate.not_before, approval.not_before, approval.review_not_before);
    const notBeforeEpochMs = Number(first(raw.not_before_epoch_ms, reviewGate.not_before_epoch_ms));
    const declaredRemaining = Number(first(raw.review_remaining_ms, raw.remaining_ms, reviewGate.remaining_ms, approval.remaining_ms));
    const parsedNotBefore = Number.isFinite(notBeforeEpochMs)
      ? notBeforeEpochMs
      : notBefore
        ? Date.parse(notBefore)
        : Number.NaN;
    const calculatedRemaining = Number.isFinite(parsedNotBefore) ? parsedNotBefore - Date.now() : 0;
    const remainingMs = Number.isFinite(declaredRemaining) ? Math.max(0, declaredRemaining) : Math.max(0, calculatedRemaining);
    const observedWorkspaceGate = normalizeWorkspaceGate(composite, raw);
    const ownerReviewSummaryDigest = first(
      raw.owner_review_summary_digest,
      ownerReviewSummary.digest,
      reviewGate.owner_review_summary_digest,
      approval.owner_review_summary_digest,
      capsule.owner_review_summary_digest,
    );
    const approvalAcknowledgements = arrayOf(first(
      raw.approval_acknowledgements,
      approval.acknowledgements,
      raw.acknowledgements,
    ));
    const approvalDigest = first(
      lineageProof.approval_digest,
      raw.approval_digest,
      raw.source_admission_approval_digest,
      approval.digest,
      approval.approval_digest,
    );
    const capsuleDigest = first(
      lineageProof.adapter_capsule_digest,
      raw.adapter_capsule_digest,
      raw.capsule_digest,
      capsule.digest,
      capsule.capsule_digest,
    );
    const activationBindingDigest = first(
      lineageProof.activation_binding_digest,
      observedWorkspaceGate.activationBindingDigest,
      raw.activation_binding_digest,
      raw.activation_binding && raw.activation_binding.digest,
    );
    const summaryDigests = [
      first(lineageProof.owner_review_summary_digest, ownerReviewSummary.digest),
      first(lineageProof.review_gate_owner_review_summary_digest, reviewGate.owner_review_summary_digest),
      first(lineageProof.approval_owner_review_summary_digest, approval.owner_review_summary_digest),
      first(lineageProof.adapter_capsule_owner_review_summary_digest, capsule.owner_review_summary_digest),
    ];
    const lineageComplete = Boolean(
      summaryDigests.every((digest) => digest && digest === ownerReviewSummaryDigest)
      && completeComponentSet(ownerReviewSummary.component_reviews)
      && approvalDigest
      && capsuleDigest
      && activationBindingDigest
      && approvalAcknowledgements.length === REQUIRED_OWNER_ACKNOWLEDGEMENTS.length
      && REQUIRED_OWNER_ACKNOWLEDGEMENTS.every(
        (value, index) => approvalAcknowledgements[index] === value,
      )
    );
    const lineageClaimed = Boolean(
      activationBindingDigest
      || observedWorkspaceGate.status === "READY_TO_FORM"
      || observedWorkspaceGate.status === "CONSUMED_BY_QUOTE_FORMATION"
    );
    const lineageIssue = lineageClaimed && !lineageComplete;
    const workspaceGate = lineageIssue
      ? { ...observedWorkspaceGate, valid: false, status: "BLOCKED_INVALID_OAC_LINEAGE" }
      : observedWorkspaceGate;
    const canonicalWritesRaw = first(
      shadow.canonical_target_writes,
      contextResidency.return_to_control_plane && contextResidency.return_to_control_plane.canonical_target_writes,
      raw.canonical_target_writes,
    );
    return {
      raw,
      status,
      adaptationRunId: first(raw.adaptation_run_id, raw.run_id, candidate.adaptation_run_id),
      candidateDigest: first(raw.candidate_digest, agentMapping.accepted_mapping_set_digest, raw.mapping_set_digest, candidate.candidate_digest, candidate.mapping_set_digest),
      snapshotDigest: first(raw.organization_snapshot_digest, raw.snapshot_digest, candidate.organization_snapshot_digest),
      demandDigest: first(raw.organizational_demand_digest, raw.demand_digest, candidate.organizational_demand_digest),
      organizationalDemand: raw.organizational_demand || {},
      admissionDigest: first(raw.source_admission_receipt_digest, raw.admission_receipt_digest, approval.receipt_digest, approval.digest),
      reviewGateDigest: first(raw.review_gate_digest, reviewGate.digest, reviewGate.review_gate_digest),
      approvalElapsedMs: Number(first(raw.approval_elapsed_since_not_before_ms)),
      approvalDigest,
      ownerReviewSummary,
      ownerReviewSummaryDigest,
      approvalAcknowledgements,
      capsuleDigest,
      activationBindingDigest,
      lineageComplete,
      lineageIssue,
      consumptionReceiptDigest: workspaceGate.consumptionReceiptDigest,
      packDigest: first(raw.pack_digest, candidate.pack_digest, capsule.pack_digest),
      executionRunId: first(workspaceGate.executionRunId, shadow.run_id, raw.execution_run_id, raw.bound_execution_run_id, capsule.execution_run_id),
      humanAuthority: first(raw.owner_ref, raw.human_authority_ref, candidate.authority_ref, approval.actor_id, API.defaultOwner),
      notBefore,
      remainingMs,
      reviewReadyAt: Number.isFinite(parsedNotBefore) ? parsedNotBefore : Date.now() + remainingMs,
      mappings,
      gaps,
      effectCeiling: first(
        contextResidency.main_agent && contextResidency.main_agent.effect_ceiling,
        raw.effect_ceiling,
        candidate.effect_ceiling,
        capsule.effect_ceiling,
        "ZERO_EXTERNAL_EFFECTS",
      ),
      canonicalWrites: canonicalWritesRaw === undefined ? null : Number(canonicalWritesRaw),
      agentMapping,
      agentStatus: first(agentMapping.status, "NOT_RUN"),
      agentModelStatus: first(agentMapping.model_status, "NOT_RUN"),
      agentModelId: first(agentMapping.model_id, "NOT_OBSERVED"),
      agentLifecycle: agentMapping.native_lifecycle || {},
      executionPolicy,
      workspaceGate,
      contextResidency,
      shadow,
      shadowStatus: first(shadow.status, "NOT_RUN"),
      shadowRunId: shadow.run_id,
      shadowReceiptDigest: shadow.receipt_digest,
      sameRunLayers: arrayOf(shadow.same_run_layers),
      agentteamsPlanDigest: shadow.agentteams_execution_plan_digest,
      plannedDomainIds: arrayOf(shadow.planned_domain_ids),
      actualAgentTeamsDomainIds: arrayOf(shadow.actual_agentteams_domain_ids),
      topologyMatch: shadow.topology_match === true,
      shadowTerminalStatus: first(shadow.terminal_status, "NOT_RUN"),
      independentVerification: shadow.independent_verification || {},
    };
  }

  function reasonCode(gap) {
    if (typeof gap === "string") return gap;
    return first(gap.reason_code, gap.code, gap.reason, gap.message);
  }

  function humanAuthorityLabel(authority) {
    const c = copy();
    const raw = String(authority || "");
    if (raw === "human:evergreen-workspace-owner") return c.ownerEvergreen;
    if (raw === API.defaultOwner) return c.ownerWorkspace;
    return raw.replace(/^human:/, "");
  }

  function businessLabel(value) {
    const c = copy();
    const raw = String(value || "").trim();
    if (!raw) return "—";
    if (c.businessTokens[raw]) return c.businessTokens[raw];
    const withoutRevision = raw.replace(/@(?:v|r)?[0-9][A-Za-z0-9._-]*$/i, "");
    if (c.businessTokens[withoutRevision]) return c.businessTokens[withoutRevision];
    if (c.domainNames[raw]) return c.domainNames[raw];
    return raw
      .replace(/^(human|principal|authority|capability|template|task|source-root):/i, "")
      .replace(/@[^\s]+$/, "")
      .replace(/[_:-]+/g, " ")
      .trim();
  }

  function isLocalPath(value) {
    const raw = String(value || "").trim();
    return /^(?:file:\/\/|\/Users\/|\/home\/|[A-Za-z]:\\)/.test(raw)
      || /(?:^|[=\s])(?:\/Users\/|\/home\/|[A-Za-z]:\\)/.test(raw);
  }

  function sampleItemLabel(value) {
    const c = copy();
    const raw = String(value || "").trim();
    if (!raw) return "—";
    if (isLocalPath(raw)) return c.hiddenLocalPath;
    const separator = currentLanguage === "en" ? ": " : "：";
    const safeField = raw.match(/^field:([^:]+):(PUBLIC|INTERNAL|CONFIDENTIAL)$/);
    if (safeField) {
      const fieldLabel = businessLabel(safeField[1]);
      const sensitivity = c.sensitivityLabels[safeField[2]] || safeField[2];
      return `${c.sampleKeys.field}${separator}${fieldLabel} · ${sensitivity}`;
    }
    const pair = raw.match(/^([A-Za-z][A-Za-z0-9_.-]*)=(.*)$/);
    if (!pair) return businessLabel(raw);
    const key = pair[1];
    const itemValue = pair[2].trim();
    const keyLabel = c.sampleKeys[key] || businessLabel(key);
    const safeKeys = new Set(["task", "task_type", "domain", "role", "actor", "owner", "target", "template"]);
    if (!safeKeys.has(key)) return `${keyLabel}${separator}${c.restrictedValue}`;
    const withoutRevision = itemValue.replace(/@(?:v|r)?[0-9][A-Za-z0-9._-]*$/i, "");
    const isBusinessReference = /^(?:human|employee|principal|authority|capability|capability-card|template|task|work|skill):/i.test(itemValue);
    const valueLabel = isLocalPath(itemValue)
      ? c.hiddenLocalPath
      : c.businessTokens[itemValue] || c.businessTokens[withoutRevision] || c.domainNames[itemValue] || isBusinessReference
        ? businessLabel(itemValue)
        : itemValue;
    return `${keyLabel}${separator}${valueLabel}`;
  }

  function translatedToken(value, catalog) {
    const raw = String(value || "");
    return catalog && catalog[raw] ? catalog[raw] : businessLabel(raw);
  }

  function replaceList(id, values, catalog) {
    const container = byId(id);
    if (!container) return;
    const items = arrayOf(values).map((value) => {
      const item = document.createElement("li");
      item.textContent = translatedToken(value, catalog);
      return item;
    });
    container.replaceChildren(...items);
  }

  function acknowledgementNodes() {
    return [byId("oac-ack-summary"), byId("oac-ack-unknowns"), byId("oac-ack-boundary")].filter(Boolean);
  }

  function completeComponentSet(items) {
    return Array.isArray(items) && items.length === SOURCE_KINDS.length
      && SOURCE_KINDS.every(kind => items.filter(item => item
        && String(first(item.component_kind, item.kind, "")).toUpperCase() === kind).length === 1);
  }

  function reviewContextComplete(state) {
    const summary = state && state.ownerReviewSummary;
    return Boolean(state && state.candidateDigest && state.ownerReviewSummaryDigest
      && summary && summary.digest === state.ownerReviewSummaryDigest
      && summary.candidate_mapping_set_digest === state.candidateDigest
      && completeComponentSet(state.mappings) && completeComponentSet(summary.component_reviews));
  }

  function acknowledgementsComplete() {
    return Boolean(
      reviewContextComplete(currentAdaptation)
      && acknowledgementNodes().length === REQUIRED_OWNER_ACKNOWLEDGEMENTS.length
      && acknowledgementNodes().every((node) => node.checked === true),
    );
  }

  function selectedAcknowledgements() {
    return acknowledgementsComplete() ? [...REQUIRED_OWNER_ACKNOWLEDGEMENTS] : [];
  }

  function renderOwnerReview(state) {
    const c = copy();
    const summary = state.ownerReviewSummary || {};
    const reviews = arrayOf(summary.component_reviews);
    const hasSummary = reviewContextComplete(state);
    const approved = Boolean(state.approvalDigest);

    setText("oac-owner-review-kicker", c.reviewKicker);
    setText("oac-owner-review-title", c.reviewTitle);
    setText(
      "oac-owner-review-detail",
      state.lineageIssue
        ? c.reviewLineageInvalid
        : !hasSummary
          ? c.reviewWaiting
          : approved
            ? c.reviewApproved
            : c.reviewReady,
    );
    setText("oac-review-organization-label", c.organizationLabel);
    setText("oac-review-domain-label", c.domainLabel);
    setText("oac-review-fact-label", c.factLabel);
    setText("oac-review-dependency-label", c.dependencyLabel);

    const maturity = first(summary.evidence_label, summary.source_maturity, "PENDING");
    const maturityNode = byId("oac-source-maturity");
    maturityNode.dataset.maturity = String(maturity);
    maturityNode.textContent = c.sourceMaturity[maturity] || c.sourceMaturityPending;
    setText("oac-review-organization", hasSummary ? businessLabel(summary.organization_id) : c.unknownsWaiting);
    const taskScope = summary.task_scope || {};
    setText("oac-review-task", hasSummary
      ? [businessLabel(taskScope.task_type), businessLabel(taskScope.deliverable_kind)].filter((item) => item !== "—").join(" → ")
      : "—");
    const domains = arrayOf(summary.domain_ids);
    setText("oac-review-domain", hasSummary ? format(c.domainsCount, { count: domains.length }) : "—");
    setText("oac-review-domain-detail", hasSummary ? domains.map(businessLabel).join(" · ") : c.componentWaiting);
    setText("oac-review-fact", hasSummary ? format(c.factsCount, { count: Number(summary.fact_count || 0) }) : "—");
    setText("oac-review-authority", hasSummary
      ? format(c.authoritiesCount, { count: arrayOf(summary.authority_refs).length })
      : c.componentWaiting);
    setText("oac-review-dependency", hasSummary
      ? format(c.dependenciesCount, {
        targets: Number(summary.dependency_target_count || 0),
        edges: Number(summary.dependency_edge_count || 0),
      })
      : "—");
    setText("oac-review-change", hasSummary
      ? format(c.changesCount, { count: arrayOf(summary.change_kinds).length })
      : c.componentWaiting);

    const container = byId("oac-review-components");
    container.setAttribute("aria-label", c.reviewComponentAria);
    container.replaceChildren(...SOURCE_KINDS.map((kind) => {
      const review = reviews.find((item) => String(item.component_kind || "").toUpperCase() === kind);
      const unknowns = arrayOf(review && review.declared_unknowns);
      const article = document.createElement("article");
      article.className = `oac-review-component ${review ? unknowns.length ? "blocked" : "complete" : "waiting"}`;
      const header = document.createElement("header");
      const name = document.createElement("span");
      name.textContent = c.sourceNames[kind];
      const badge = document.createElement("b");
      badge.textContent = !review ? c.componentWaiting : unknowns.length ? c.componentBlocked : c.componentComplete;
      header.append(name, badge);
      const effect = document.createElement("strong");
      effect.textContent = review
        ? arrayOf(review.contract_effects).map((item) => translatedToken(item, c.componentEffects)).join(" · ")
        : c.componentWaiting;
      const observed = document.createElement("small");
      observed.textContent = review ? format(c.observedItems, { count: Number(review.observed_item_count || 0) }) : "—";
      const samples = document.createElement("ul");
      arrayOf(review && review.sample_items).slice(0, 3).forEach((sample) => {
        const item = document.createElement("li");
        item.textContent = sampleItemLabel(sample);
        samples.append(item);
      });
      const owners = document.createElement("small");
      const ownerLabels = arrayOf(review && review.responsible_refs).map(businessLabel);
      owners.textContent = review && ownerLabels.length ? format(c.responsibleFor, { owners: ownerLabels.join(" · ") }) : "—";
      article.append(header, effect, observed, samples, owners);
      return article;
    }));
    setText("oac-review-runtime-boundary", c.runtimeProjectionBoundary);

    setText("oac-review-unknowns-label", c.unknownsLabel);
    const declaredUnknowns = arrayOf(summary.declared_unknowns);
    const unknownCard = byId("oac-review-unknowns");
    unknownCard.dataset.state = !hasSummary ? "WAITING" : declaredUnknowns.length ? "OPEN" : "CLEAR";
    setText("oac-review-unknowns-title", !hasSummary
      ? c.unknownsWaiting
      : declaredUnknowns.length
        ? format(c.unknownsCount, { count: declaredUnknowns.length })
        : c.unknownsClear);
    replaceList(
      "oac-review-unknowns-list",
      !hasSummary ? [c.reviewWaiting] : declaredUnknowns.length ? declaredUnknowns : [c.unknownsClearDetail],
    );
    setText("oac-review-effects-label", c.effectsLabel);
    setText("oac-review-non-effects-label", c.nonEffectsLabel);
    replaceList("oac-review-effects", hasSummary ? summary.approval_effects : [c.componentWaiting], c.effects);
    replaceList("oac-review-non-effects", hasSummary ? summary.non_effects : [c.componentWaiting], c.nonEffects);

    const acknowledgementBox = byId("oac-review-acknowledgements");
    acknowledgementBox.hidden = !hasSummary;
    setText(
      "oac-review-acknowledgements-title",
      approved ? c.acknowledgementsRecordedTitle : c.acknowledgementsTitle,
    );
    setText("oac-ack-summary-label", c.acknowledgementLabels[0]);
    setText("oac-ack-unknowns-label", c.acknowledgementLabels[1]);
    setText("oac-ack-boundary-label", c.acknowledgementLabels[2]);
    setText(
      "oac-review-acknowledgements-note",
      approved ? c.acknowledgementsRecordedNote : c.acknowledgementsNote,
    );
    const reviewSubject = JSON.stringify([
      state.workspaceGate.executionRunId, state.humanAuthority,
      state.candidateDigest, state.ownerReviewSummaryDigest,
      window.OrgRebaseClient.session()?.principal?.actor_id,
    ]);
    const subjectChanged = renderedReviewSubject !== reviewSubject;
    renderedReviewSubject = reviewSubject;
    const recorded = new Set(state.approvalAcknowledgements);
    acknowledgementNodes().forEach((node, index) => {
      node.disabled = approved || !hasSummary || !operatorCanGovern(state.humanAuthority);
      if (approved) node.checked = recorded.has(REQUIRED_OWNER_ACKNOWLEDGEMENTS[index]);
      else if (subjectChanged || !hasSummary) node.checked = false;
    });
  }

  function mappingFor(kind) {
    if (!currentAdaptation) return null;
    return currentAdaptation.mappings.find((item) => String(first(item.component_kind, item.kind, "")).toUpperCase() === kind) || null;
  }

  function mappingStatus(mapping) {
    if (!mapping) return "waiting";
    const reasons = arrayOf(first(mapping.declared_unknowns, mapping.gaps, mapping.reason_codes));
    return reasons.length ? "blocked" : "complete";
  }

  function renderSources() {
    const container = byId("oac-source-grid");
    if (!container) return;
    const c = copy();
    container.setAttribute("aria-label", c.sourceGridLabel);
    container.replaceChildren(...SOURCE_KINDS.map((kind) => {
      const mapping = mappingFor(kind);
      const state = mappingStatus(mapping);
      const article = document.createElement("article");
      article.className = `oac-source-card ${state}`;
      const header = document.createElement("header");
      const label = document.createElement("span");
      label.textContent = c.sourceNames[kind];
      const badge = document.createElement("b");
      badge.textContent = state === "complete" ? c.sourceComplete : state === "blocked" ? c.sourceBlocked : c.sourceWaiting;
      header.append(label, badge);
      const title = document.createElement("strong");
      const exactSource = mapping ? first(mapping.source_root_ref, mapping.producer, mapping.task_id) : null;
      title.textContent = exactSource || c.sourceCandidate;
      if (exactSource) title.title = String(exactSource);
      const digest = document.createElement("small");
      const exactDigest = mapping ? first(mapping.output_digest, mapping.mapping_digest, mapping.source_digest) : null;
      digest.textContent = exactDigest ? compact(exactDigest, 20) : c.noDigest;
      if (exactDigest) digest.title = String(exactDigest);
      const target = document.createElement("small");
      const targets = arrayOf(first(mapping && mapping.target_oac_paths, mapping && mapping.target_paths));
      target.textContent = `${c.targetPath}: ${targets.length ? targets.join(", ") : "—"}`;
      if (targets.length) target.title = targets.join("\n");
      article.append(header, title, digest, target);
      return article;
    }));
  }

  function effectiveStep(status) {
    if (currentAdaptation && currentAdaptation.lineageIssue) return 4;
    if (currentAdaptation && currentAdaptation.workspaceGate.status === "CONSUMED_BY_QUOTE_FORMATION") return 6;
    if (currentAdaptation && currentAdaptation.activationBindingDigest) return 5;
    if (status === "SHADOW_COMPLETED" || status === "READY_FOR_SHADOW") return 5;
    if (status === "OWNER_REVIEW_PENDING") return 3;
    if (status === "AGENT_MAPPING_HOLD") return currentAdaptation && currentAdaptation.mappings.length ? 2 : 1;
    return currentAdaptation && currentAdaptation.mappings.length ? 2 : 1;
  }

  function renderRail() {
    const c = copy();
    const step = effectiveStep(currentAdaptation ? currentAdaptation.status : "NOT_STARTED");
    const blocked = currentAdaptation && currentAdaptation.status === "AGENT_MAPPING_HOLD";
    document.querySelectorAll("#oac-adaptation-rail article").forEach((node, index) => {
      node.classList.toggle("complete", index < step || step >= STEP_IDS.length);
      node.classList.toggle("active", index === step && step < STEP_IDS.length && !blocked);
      node.classList.toggle("blocked", index === step && blocked);
      setText(`oac-step-${STEP_IDS[index]}-title`, c.steps[index]);
      setText(`oac-step-${STEP_IDS[index]}-detail`, c.stepDetails[index]);
    });
    byId("oac-adaptation-rail").setAttribute("aria-label", c.title);
  }

  function remainingMs() {
    if (!currentAdaptation) return 0;
    return Math.max(0, (currentAdaptation.reviewReadyAt || Date.now()) - Date.now());
  }

  function mappingFailureCopy(state) {
    if (!state || !["PROVIDER_ERROR", "SCHEMA_ERROR"].includes(state.agentModelStatus)) return null;
    const c = copy();
    if (state.agentModelStatus === "SCHEMA_ERROR") return c.mappingSchemaFailed;
    return arrayOf(state.agentMapping.validation_reason_codes).includes("VERTEX_HTTP_ERROR:429")
      ? c.mappingRateLimited : c.mappingServiceFailed;
  }

  function operatorCanGovern(owner = null) {
    const session = window.OrgRebaseClient.session();
    if (!session) return false;
    if (!session.authentication_required) return session.mode === "local";
    const principal = session.principal;
    return Boolean(session.authenticated && principal
      && (!owner || principal.actor_id === owner)
      && principal.roles?.some(role => ["governor", "administrator"].includes(role)));
  }

  function renderAction() {
    const button = byId("oac-primary-action");
    if (!button) return;
    const c = copy();
    const status = currentAdaptation ? currentAdaptation.status : "NOT_STARTED";
    const policyAction = policyActionForStatus(currentAdaptation);
    button.disabled = requestInFlight;
    button.dataset.action = "agent-prepare";
    button.removeAttribute("title");
    if (requestInFlight) {
      button.textContent = activeAction === "approve"
        ? c.approving
        : activeAction === "agent-prepare" || activeAction === "structured-prepare"
          ? c.preparing
          : c.continueToQuote;
      return;
    }
    if (status === "OWNER_REVIEW_PENDING") {
      const seconds = Math.ceil(remainingMs() / 1000);
      const summaryReady = reviewContextComplete(currentAdaptation);
      const acknowledged = acknowledgementsComplete();
      button.dataset.action = "approve";
      button.disabled = seconds > 0 || !summaryReady || !acknowledged || !operatorCanGovern(currentAdaptation.humanAuthority);
      button.textContent = seconds > 0 ? format(c.review, { seconds }) : c.approve;
      if (!operatorCanGovern(currentAdaptation.humanAuthority)) button.title = currentLanguage === "en"
        ? `Required owner: ${businessLabel(currentAdaptation.humanAuthority)}` : `需由${businessLabel(currentAdaptation.humanAuthority)}确认`;
      else if (!summaryReady) button.title = c.summaryMissing;
      else if (!acknowledged) button.title = c.acknowledgementMissing;
      return;
    }
    if (status === "READY_FOR_SHADOW" || status === "SHADOW_COMPLETED") {
      button.dataset.action = "continue-quote";
      const gate = currentAdaptation.workspaceGate;
      button.disabled = !(gate.formAllowed || (gate.valid && gate.status === "CONSUMED_BY_QUOTE_FORMATION"
        && gate.consumptionReceiptDigest)) || !window.OrgRebaseWorkspaceShell;
      button.textContent = c.continueToQuote;
      return;
    }
    if (status === "AGENT_MAPPING_HOLD") {
      button.disabled = true;
      button.textContent = mappingFailureCopy(currentAdaptation) ? c.mappingNoCandidate : c.held;
      return;
    }
    const actionAllowed = Boolean(policyAction && policyAllows(currentAdaptation, policyAction));
    button.dataset.action = policyAction === POLICY_ACTIONS.structured ? "structured-prepare" : "agent-prepare";
    button.disabled = !actionAllowed || !operatorCanGovern();
    button.textContent = actionAllowed ? policyActionLabel(currentAdaptation, policyAction) : c.actionDisabled;
    if (!actionAllowed && policyAction) button.title = policyBlockReason(currentAdaptation, policyAction).naturalReason || policyBlockReason(currentAdaptation, policyAction).stableReason;
  }

  function renderEnhancedAction() {
    const button = byId("oac-enhanced-action");
    if (!button) return;
    const c = copy();
    const state = currentAdaptation;
    button.dataset.action = "execute-shadow";
    button.removeAttribute("title");
    if (!state || !["READY_FOR_SHADOW", "SHADOW_COMPLETED"].includes(state.status)) {
      button.disabled = true;
      button.textContent = c.enhancedWaiting;
      return;
    }
    if (requestInFlight) {
      button.disabled = true;
      button.textContent = activeAction === "execute-shadow" ? c.executingShadow : c.enhancedWaiting;
      return;
    }
    const verificationPassed = state.status === "SHADOW_COMPLETED"
      && state.independentVerification.status === "PASS";
    const actionAllowed = policyAllows(state, POLICY_ACTIONS.shadow);
    button.disabled = verificationPassed || !actionAllowed || !operatorCanGovern();
    button.textContent = verificationPassed
      ? c.shadowVerified
      : actionAllowed
        ? state.status === "SHADOW_COMPLETED" && state.executionPolicy.mode !== "FROZEN_REPLAY"
          ? c.runIndependentVerification
          : policyActionLabel(state, POLICY_ACTIONS.shadow)
        : c.actionDisabled;
    if (!verificationPassed && !actionAllowed) {
      const blocked = policyBlockReason(state, POLICY_ACTIONS.shadow);
      button.title = blocked.naturalReason || blocked.stableReason;
    }
  }

  function ensureCountdown() {
    if (countdownTimer) window.clearInterval(countdownTimer);
    countdownTimer = null;
    if (!currentAdaptation || currentAdaptation.status !== "OWNER_REVIEW_PENDING" || remainingMs() <= 0) return;
    countdownTimer = window.setInterval(() => {
      renderAction();
      renderEnhancedAction();
      if (remainingMs() <= 0) {
        window.clearInterval(countdownTimer);
        countdownTimer = null;
      }
    }, 250);
  }

  function renderContext(state) {
    const c = copy();
    const context = state.contextResidency || {};
    const manager = context.main_agent || {};
    const resident = arrayOf(context.resident_domain_contracts);
    const projections = arrayOf(context.task_projections);
    const compiled = context.status === "READY";
    setText("oac-context-kicker", c.contextKicker);
    setText("oac-context-title", c.contextTitle);
    setText("oac-context-status", compiled ? c.contextCompiled
      : context.status === "EVIDENCE_UNANCHORED" ? c.contextUnanchored : c.contextWaiting);
    setText("oac-context-manager-title", c.managerTitle);
    const demand = state.organizationalDemand || {};
    const objective = first(demand.objective, c.unknownObjective);
    const topologyDetail = state.topologyMatch
      ? format(c.topologyExact, {
        planned: state.plannedDomainIds.length,
        actual: state.actualAgentTeamsDomainIds.length,
      })
      : manager.formation_decision_receipt_digest
        ? c.topologyBound
        : c.topologyPending;
    setText("oac-context-manager-detail", format(c.managerDetail, {
      objective: objective === "produce one governed enterprise quote with zero external effects"
        ? c.governedQuoteObjective
        : objective,
      obligations: Number(first(demand.evidence_obligation_count, 0)),
    }) + (manager.formation_decision_receipt_digest ? ` · ${c.managerFormationBound}` : "") + ` · ${topologyDetail}`);
    setText("oac-context-manager-digest", compiled ? c.managerContextBound : "—");
    setTitle("oac-context-manager-digest");
    setText("oac-context-resident-title", c.residentTitle);
    setText("oac-context-resident-detail", format(c.residentDetail, { count: resident.length }));
    setText("oac-context-resident-digest", compiled
      ? `${resident.map((item) => c.domainNames[item.domain_id] || item.domain_id).join(" · ")} · ${format(c.contractsBound, { count: resident.length })}`
      : "—");
    setTitle("oac-context-resident-digest");
    setText("oac-context-task-title", c.taskTitle);
    setText("oac-context-task-detail", format(c.taskDetail, { count: projections.length }));
    setText("oac-context-task-digest", compiled
      ? `${projections.map((item) => c.domainNames[item.domain_id] || item.domain_id).join(" · ")} · ${format(c.projectionsBound, { count: projections.length })}`
      : "—");
    setTitle("oac-context-task-digest");
    setText("oac-context-return-title", c.returnTitle);
    setText("oac-context-return-detail", c.returnDetail);
    setText("oac-context-return-digest", c.writesZero);
  }

  function observedCausalFacts(state) {
    const c = copy();
    const context = state.contextResidency || {};
    const resident = arrayOf(context.resident_domain_contracts);
    const facts = [];
    if (state.mappings.length) facts.push(format(c.causalInputs, { count: state.mappings.length }));
    if (resident.length) facts.push(format(c.causalDomains, { count: resident.length }));
    if (context.status === "READY") facts.push(c.causalContext);
    if (state.approvalDigest) facts.push(c.causalHuman);
    if (state.sameRunLayers.includes("TOOL")) facts.push(c.causalTool);
    if (state.sameRunLayers.includes("SKILL")) facts.push(c.causalSkill);
    return facts.length ? facts.join(" · ") : c.causalWaiting;
  }

  function renderBusinessGate(state) {
    const c = copy();
    const gate = state.workspaceGate;
    const card = byId("oac-business-gate");
    card.dataset.gateState = gate.status;
    setText("oac-product-business-label", c.productBusinessLabel);
    let title = c.businessBlocked;
    let detail = gate.valid ? c.businessBlockedDetail : c.businessUnknownDetail;
    if (state.lineageIssue) {
      title = c.businessLineageInvalid;
      detail = c.businessLineageInvalidDetail;
    } else if (gate.valid && gate.status === "CONSUMED_BY_QUOTE_FORMATION") {
      title = c.businessConsumed;
      detail = gate.mode === "optional"
        ? c.businessConsumedOptionalDetail
        : c.businessConsumedDetail;
    } else if (gate.valid && gate.mode === "optional") {
      title = c.businessOptional;
      detail = c.businessOptionalDetail;
    } else if (gate.valid && gate.status === "READY_TO_FORM") {
      title = c.businessReady;
      detail = c.businessReadyDetail;
    }
    setText("oac-product-business", title);
    setText("oac-product-business-detail", `${detail} · ${observedCausalFacts(state)}`);
    byId("oac-product-business-detail").removeAttribute("title");
  }

  function render({ announceChange = false } = {}) {
    const c = copy();
    const state = currentAdaptation || normalize({ status: "NOT_STARTED" });
    const root = byId("oac-adaptation");
    root.dataset.state = state.status;
    root.dataset.gateState = state.workspaceGate.status;
    setText("oac-adaptation-kicker", c.kicker);
    setText("oac-adaptation-title", c.title);
    const shadowStatus = state.status === "SHADOW_COMPLETED"
      ? state.independentVerification.status === "PASS"
        ? c.shadowVerified
        : c.shadowCompletedPending
      : c.status[state.status] || state.status;
    const gateStatus = state.lineageIssue
      ? c.businessLineageInvalid
      : !state.workspaceGate.valid
        ? c.businessBlocked
      : state.workspaceGate.mode === "required" && state.workspaceGate.status === "BLOCKED_PENDING_OAC"
        ? c.businessBlocked
        : state.workspaceGate.status === "READY_TO_FORM"
          ? c.businessReady
          : state.workspaceGate.status === "CONSUMED_BY_QUOTE_FORMATION"
            ? c.businessConsumed
            : shadowStatus;
    const modelFailure = mappingFailureCopy(state);
    const visibleStatus = actionError
      ? `${c.requestFailed} · ${actionError}`
      : serviceError
        ? `${c.statusUnavailable} · ${serviceError}`
        : modelFailure || gateStatus;
    setText("oac-adaptation-status", visibleStatus);
    setText("oac-adaptation-intro-label", c.introLabel);
    setText("oac-adaptation-intro-title", c.introTitle);
    setText("oac-review-jump", c.reviewJump);
    setText("oac-adaptation-intro-body", c.introBodyByMode[state.executionPolicy.mode]);
    setText("oac-verification-title", c.auditTitle);
    setText("oac-verification-detail", c.auditDetail);
    setText("oac-enhanced-title", c.enhancedTitle);
    setText("oac-enhanced-detail", c.enhancedDetail);
    setText("oac-product-gap-label", c.productGapLabel);
    setText("oac-product-validation-label", c.productValidationLabel);
    setText("oac-product-validation-detail", c.productValidationDetail);
    setText("oac-product-admission-label", c.productAdmissionLabel);
    setText("oac-product-admission-detail", c.productAdmissionDetail);
    setText("oac-product-activation-label", c.productActivationLabel);
    setText("oac-product-activation-detail", c.productActivationDetail);
    setText("oac-product-shadow-label", c.productShadowLabel);
    setText("oac-product-shadow-detail", c.productShadowDetail);
    setText("oac-product-effect-label", c.productEffectLabel);
    byId("oac-product-outcomes").setAttribute("aria-label", c.productOutcomesLabel);
    renderBusinessGate(state);
    setText("oac-fact-run-label", c.runLabel);
    setText("oac-fact-run", compact(state.adaptationRunId, 30));
    setTitle("oac-fact-run", state.adaptationRunId);
    setText(
      "oac-fact-run-detail",
      state.workspaceGate.status === "CONSUMED_BY_QUOTE_FORMATION"
        ? c.runDetailConsumed
        : state.workspaceGate.mode === "required" && state.activationBindingDigest
          ? c.runDetailBound
          : state.workspaceGate.mode === "required"
            ? c.runDetailRequiredPending
            : c.runDetailSeparate,
    );
    const liveAgent = state.agentStatus === "VALIDATED_CANDIDATE" && state.agentModelStatus === "VALID";
    const suggestion = modelSuggestionLabel(state);
    const structured = state.executionPolicy.mappingSource === "STRUCTURED_MATERIALS";
    const mappingIncomplete = state.executionPolicy.mappingSource === "INCOMPLETE_MAPPING_EVIDENCE"
      || state.agentStatus === "EVIDENCE_INCOMPLETE";
    setText("oac-fact-agent-label", structured ? c.structuredLabel : c.agentLabel);
    const structuredCandidate = structured && Boolean(state.candidateDigest) && completeComponentSet(state.mappings);
    const agentStatusCopy = modelFailure || (mappingIncomplete ? c.providerIncomplete : structured
      ? structuredCandidate ? c.structuredReady : c.structuredWaiting
      : liveAgent
      ? format(c.agentVerified, { suggestion })
      : state.agentStatus === "HOLD"
        ? c.agentHold
        : c.agentNotRun);
    const agentDetailCopy = modelFailure ? c.mappingNoCandidate : mappingIncomplete ? c.mappingSources.INCOMPLETE : structured ? c.structuredDetail : format(c.agentDetail, {
      runtime: first(state.agentLifecycle.runtime, "AgentTeams v1.2.2"),
      actions: Number(first(state.agentLifecycle.action_count, 0)),
      model: `${suggestion} · ${state.agentModelId} · ${c.modelStatuses[state.agentModelStatus] || state.agentModelStatus}`,
    });
    setText("oac-fact-agent", agentStatusCopy);
    setText("oac-fact-agent-detail", agentDetailCopy);
    setTitle("oac-fact-agent-detail", state.agentMapping.receipt_digest);
    setText("oac-product-agent-label", structured ? c.structuredLabel : c.agentLabel);
    setText("oac-product-agent", agentStatusCopy);
    setText("oac-product-agent-detail", modelFailure || mappingIncomplete || structured || liveAgent ? agentDetailCopy : c.productAgentWaiting);
    setTitle("oac-product-agent-detail", liveAgent ? state.agentMapping.receipt_digest : null);
    setText("oac-fact-contract-label", c.contractLabel);
    const contractDigests = [state.candidateDigest, state.demandDigest, state.approvalDigest].filter(Boolean);
    setText("oac-fact-contract", contractDigests.map((item) => compact(item, 10)).join(" → ") || c.noDigest);
    setTitle("oac-fact-contract", ...contractDigests);
    setText("oac-fact-contract-detail", format(c.contractDetail, {
      gate: compact(state.reviewGateDigest, 10),
      wait: Number.isFinite(state.approvalElapsedMs)
        ? `${(state.approvalElapsedMs / 1000 + 4).toFixed(1)}s`
        : "—",
      approval: compact(state.approvalDigest, 10),
    }));
    setTitle("oac-fact-contract-detail", state.reviewGateDigest, state.admissionDigest, state.approvalDigest);
    setText("oac-fact-gap-label", c.gapLabel);
    const held = state.status === "AGENT_MAPPING_HOLD" || state.agentStatus === "HOLD"
      || state.agentStatus === "EVIDENCE_INCOMPLETE";
    const candidateValidated = !held && reviewContextComplete(state)
      && ["OWNER_REVIEW_PENDING", "READY_FOR_SHADOW", "SHADOW_COMPLETED"].includes(state.status);
    const gapLabel = modelFailure && !state.gaps.length ? c.mappingNotAssessed : state.gaps.length ? format(c.gapCount, { count: state.gaps.length })
      : candidateValidated ? c.gapNone : state.status === "NOT_STARTED" ? c.gapChecking : c.gapUnavailable;
    setText("oac-fact-gap", gapLabel);
    setText("oac-fact-gap-detail", state.gaps.length || candidateValidated
      ? state.gaps.map(reasonCode).filter(Boolean).join(" · ") || c.noReason
      : "—");
    setText("oac-product-gap", gapLabel);
    setText("oac-product-gap-detail", modelFailure && !state.gaps.length ? c.mappingNoCandidate : held || state.gaps.length ? c.productGapBlocked
      : candidateValidated ? c.productGapClear : c.productGapWaiting);
    setText("oac-product-validation", modelFailure ? c.mappingNoCandidate : held || state.gaps.length ? c.productValidationHold
      : candidateValidated ? c.productValidationPass
        : state.status === "NOT_STARTED" ? c.productValidationWaiting : c.productValidationUnknown);
    const admissionComplete = ["READY_FOR_SHADOW", "SHADOW_COMPLETED"].includes(state.status);
    setText("oac-product-admission", admissionComplete
      ? c.productAdmissionComplete
      : state.status === "OWNER_REVIEW_PENDING"
        ? c.productAdmissionPending
        : state.status === "AGENT_MAPPING_HOLD"
          ? c.productAdmissionBlocked
          : c.productAdmissionWaiting);
    setText("oac-product-activation", state.workspaceGate.status === "CONSUMED_BY_QUOTE_FORMATION"
      ? c.productActivationConsumed
      : state.activationBindingDigest || admissionComplete
        ? c.productActivationReady
        : c.productActivationWaiting);
    setText("oac-product-shadow", state.status === "SHADOW_COMPLETED"
      ? shadowStatus
      : state.status === "READY_FOR_SHADOW"
        ? c.productShadowReady
        : c.productShadowNotRun);
    setText("oac-fact-effect-label", c.effectLabel);
    const writesObserved = state.status !== "NOT_STARTED" && Number.isFinite(state.canonicalWrites);
    const effectTitle = writesObserved
      ? (state.effectCeiling === "ZERO_EXTERNAL_EFFECTS" ? c.effectZero : state.effectCeiling)
      : c.effectDeclared;
    const effectDetail = writesObserved
      ? format(c.effectDetail, { writes: state.canonicalWrites })
      : c.effectDeclaredDetail;
    setText("oac-fact-effect", effectTitle);
    setText("oac-fact-effect-detail", effectDetail);
    setText("oac-product-effect", effectTitle);
    setText("oac-product-effect-detail", effectDetail);
    setText("oac-fact-binding-label", c.bindingLabel);
    const bound = state.capsuleDigest && (state.executionRunId || state.status === "READY_FOR_SHADOW");
    setText("oac-fact-binding", bound
      ? `${compact(state.adaptationRunId, 10)} → ${compact(state.capsuleDigest, 10)} → ${state.executionRunId ? compact(state.executionRunId, 10) : "…"}`
      : c.bindingNone);
    setTitle(
      "oac-fact-binding",
      state.adaptationRunId,
      state.capsuleDigest,
      state.executionRunId,
      state.activationBindingDigest,
      state.consumptionReceiptDigest,
    );
    const sameRunDetail = state.sameRunLayers.length
      ? state.sameRunLayers.map((layer) => c.layerNames[layer] || layer).join("→")
      : state.status === "READY_FOR_SHADOW" ? c.bindingReady : c.bindingDetail;
    const independent = state.independentVerification || {};
    const verificationDetail = independent.status === "PASS"
      ? format(c.independentVerified, {
        outputs: Number(independent.checked_output_file_count || 0),
        packs: Number(independent.checked_pack_file_count || 0),
      })
      : null;
    const consumptionDetail = state.workspaceGate.status === "CONSUMED_BY_QUOTE_FORMATION"
      ? format(c.bindingConsumed, {
        activation: compact(state.activationBindingDigest, 12),
        consumption: compact(state.consumptionReceiptDigest, 12),
      })
      : null;
    setText(
      "oac-fact-binding-detail",
      [consumptionDetail, sameRunDetail, verificationDetail].filter(Boolean).join(" · "),
    );
    setText("oac-control-owner-label", c.ownerLabel);
    setText("oac-control-owner", humanAuthorityLabel(state.humanAuthority));
    byId("oac-control-owner").removeAttribute("title");
    const policyAction = policyActionForStatus(state);
    const blocked = policyAction && !policyAllows(state, policyAction)
      ? policyBlockReason(state, policyAction)
      : null;
    setText("oac-control-note", modelFailure ? c.mappingFailureRecovery : blocked ? blocked.naturalReason : c.controlNote);
    setTitle("oac-control-note");
    renderRefresh();
    setText("oac-adaptation-boundary", format(c.policyBoundary, {
      mode: c.policyModes[state.executionPolicy.mode],
      source: mappingSourceLabel(state),
      suggestion,
      calls: externalCallBoundary(state),
    }));
    renderRail();
    renderOwnerReview(state);
    renderSources();
    renderContext(state);
    renderAction();
    renderEnhancedAction();
    ensureCountdown();
    if (announceChange) {
      window.dispatchEvent(new CustomEvent("orgrebase:oacadaptationchange", { detail: state }));
    }
  }

  async function request(path, { method = "GET", body, actor } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (actor) headers[API.actorHeader] = actor;
    const response = await window.OrgRebaseClient.fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let payload = null;
    try { payload = await window.OrgRebaseClient.readBody(response); }
    catch (error) { if (error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") throw error; }
    if (!response.ok) {
      const detail = payload && (payload.detail || payload.error || payload.message);
      const deploymentCodes = new Set([
        "QUOTE_PARITY_GOLDEN_SUMMARY_UNREADABLE",
        "OAC_ADAPTATION_IMPLEMENTATION_BINDING_MISSING",
        "OAC_ADAPTATION_READY_DRAFT_PACK_MISSING",
      ]);
      const staleCodes = new Set([
        "OAC_ADAPTATION_CANDIDATE_DIGEST_MISMATCH",
        "OAC_ADAPTATION_MAPPING_SET_DIGEST_MISMATCH",
        "OAC_ADAPTATION_PACK_BINDING_STALE",
        "OAC_ADAPTATION_PROFILE_BINDING_STALE",
        "OAC_ADAPTATION_RULE_SET_DIGEST_MISMATCH",
      ]);
      const publicCode = detail && typeof detail === "object" && typeof detail.code === "string"
        && /^[A-Z][A-Z0-9_]{2,100}$/.test(detail.code) ? detail.code : null;
      // Only the known integrity wrapper can carry an explicitly public nested reason.
      // Arbitrary message text may contain paths, provider output or credentials.
      const nestedCode = publicCode === "EVIDENCE_INTEGRITY_FAILED"
        && (deploymentCodes.has(detail.message) || staleCodes.has(detail.message))
        ? detail.message : null;
      const code = nestedCode || publicCode;
      const messageTemplate = deploymentCodes.has(code) ? copy().deploymentError
        : code && /DIGEST_MISMATCH|STALE|CHANGED/.test(code) ? copy().staleRequest : copy().apiError;
      const detailMessage = code ? messageTemplate.replace("{code}", code) : null;
      const error = new Error(detailMessage || `${copy().requestFailed} (${response.status})`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload || {};
  }

  function renderRefresh() {
    const button = byId("oac-refresh");
    button.textContent = refreshRequest ? copy().refreshing : copy().refresh;
    button.disabled = requestInFlight || Boolean(refreshRequest);
    button.setAttribute("aria-busy", String(button.disabled));
  }

  function invalidateRefresh() {
    ++refreshVersion;
    refreshRequest = null;
  }

  function clearSessionView() {
    invalidateRefresh();
    ++commandVersion;
    currentAdaptation = null;
    renderedReviewSubject = null;
    requestInFlight = false;
    refreshAfterAction = false;
    activeAction = null;
    serviceError = null;
    actionError = null;
    acknowledgementNodes().forEach(node => { node.checked = false; });
    render({ announceChange: true });
  }

  function refresh({ force = false } = {}) {
    if (!force && requestInFlight) return refreshRequest || Promise.resolve();
    if (!force && refreshRequest) return refreshRequest;
    const version = ++refreshVersion;
    refreshAfterAction = false;
    const pending = (async () => {
      try {
        const payload = await request(API.status);
        if (version !== refreshVersion) return;
        currentAdaptation = normalize(payload);
        serviceError = null;
        render({ announceChange: true });
      } catch (error) {
        if (version !== refreshVersion || error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
        serviceError = error.status || "OFFLINE";
        if (!currentAdaptation) currentAdaptation = normalize({ status: "NOT_STARTED" });
        render();
      } finally {
        if (version === refreshVersion) {
          refreshRequest = null;
          renderRefresh();
        }
      }
    })();
    refreshRequest = pending;
    renderRefresh();
    return pending;
  }

  async function runPrimaryAction() {
    if (requestInFlight) return;
    const button = byId("oac-primary-action");
    const action = button.dataset.action || "agent-prepare";
    if (action === "continue-quote") {
      window.OrgRebaseWorkspaceShell.navigate("quote");
      return;
    }
    if (!operatorCanGovern(action === "approve" ? currentAdaptation?.humanAuthority : null)) return;
    const policyAction = action === "structured-prepare" ? POLICY_ACTIONS.structured
      : action === "agent-prepare" ? POLICY_ACTIONS.prepare : null;
    if (policyAction && !policyAllows(currentAdaptation, policyAction)) {
      const blocked = policyBlockReason(currentAdaptation, policyAction);
      actionError = blocked.naturalReason;
      render();
      return;
    }
    if (action === "approve" && !acknowledgementsComplete()) {
      actionError = reviewContextComplete(currentAdaptation)
        ? copy().acknowledgementMissing
        : copy().summaryMissing;
      render();
      return;
    }
    requestInFlight = true;
    const command = ++commandVersion;
    invalidateRefresh();
    activeAction = action;
    actionError = null;
    renderRefresh();
    renderAction();
    renderEnhancedAction();
    let actionStateChanged = false;
    try {
      let payload;
      if (action === "approve") {
        const actor = window.OrgRebaseClient.session()?.principal?.actor_id
          || currentAdaptation.humanAuthority || API.defaultOwner;
        const candidateDigest = currentAdaptation.candidateDigest;
        payload = await request(API.approve, {
          method: "POST",
          actor,
          body: {
            actor_id: actor,
            candidate_digest: candidateDigest,
            owner_review_summary_digest: currentAdaptation.ownerReviewSummaryDigest,
            acknowledgements: selectedAcknowledgements(),
            command_id: commandId("oac-admit"),
          },
        });
      } else {
        const structured = action === "structured-prepare";
        payload = await request(structured ? API.structuredPrepare : API.agentPrepare, {
          method: "POST",
          body: { command_id: commandId(structured ? "oac-structured-prepare" : "oac-agent-prepare") },
        });
      }
      if (command !== commandVersion) return;
      currentAdaptation = normalize(payload);
      serviceError = null;
      actionStateChanged = true;
      if (action === "approve" || action === "structured-prepare") {
        actionStateChanged = false;
        await refresh({ force: true });
      }
    } catch (error) {
      if (command !== commandVersion || error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
      actionError = error.message;
      actionStateChanged = false;
      await refresh({ force: true });
    } finally {
      if (command !== commandVersion) return;
      requestInFlight = false;
      activeAction = null;
      if (refreshAfterAction) {
        actionStateChanged = false;
        await refresh({ force: true });
      }
      render({ announceChange: actionStateChanged });
    }
  }

  async function runEnhancedAction() {
    if (requestInFlight || !currentAdaptation || !operatorCanGovern()) return;
    if (!policyAllows(currentAdaptation, POLICY_ACTIONS.shadow)) {
      const blocked = policyBlockReason(currentAdaptation, POLICY_ACTIONS.shadow);
      actionError = blocked.naturalReason;
      render();
      return;
    }
    requestInFlight = true;
    const command = ++commandVersion;
    invalidateRefresh();
    activeAction = "execute-shadow";
    actionError = null;
    renderRefresh();
    renderAction();
    renderEnhancedAction();
    let actionStateChanged = false;
    try {
      const payload = await request(API.executeShadow, { method: "POST", body: {} });
      if (command !== commandVersion) return;
      currentAdaptation = normalize(payload);
      serviceError = null;
      actionStateChanged = true;
    } catch (error) {
      if (command !== commandVersion || error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") return;
      actionError = error.message;
      await refresh({ force: true });
    } finally {
      if (command !== commandVersion) return;
      requestInFlight = false;
      activeAction = null;
      if (refreshAfterAction) {
        actionStateChanged = false;
        await refresh({ force: true });
      }
      render({ announceChange: actionStateChanged });
    }
  }

  window.addEventListener("orgrebase:sessionended", clearSessionView);
  window.addEventListener("orgrebase:sessionchange", event => {
    const next = sessionIdentity(event.detail);
    if (next === currentSessionIdentity) return;
    currentSessionIdentity = next;
    clearSessionView();
    if (event.detail && (!event.detail.authentication_required || event.detail.authenticated)) refresh({ force: true });
  });
  window.addEventListener("orgrebase:languagechange", (event) => {
    currentLanguage = event.detail && event.detail.language === "en" ? "en" : "zh-CN";
    render();
  });
  window.addEventListener("orgrebase:workspacechange", () => {
    if (requestInFlight) {
      refreshAfterAction = true;
      invalidateRefresh();
      renderRefresh();
      return;
    }
    refresh({ force: true });
  });
  const preflightMount = byId("oac-preflight-mount");
  if (preflightMount) preflightMount.replaceWith(byId("oac-adaptation"));
  byId("oac-review-jump").addEventListener("click", () => {
    const confirmations = byId("oac-review-acknowledgements");
    const target = confirmations && !confirmations.hidden
      ? confirmations : byId("oac-adaptation-control");
    if (!target) return;
    target.scrollIntoView({ block: "start", behavior: "instant" });
    target.focus({ preventScroll: true });
  });
  byId("oac-refresh").addEventListener("click", () => refresh());
  byId("oac-primary-action").addEventListener("click", runPrimaryAction);
  byId("oac-enhanced-action").addEventListener("click", runEnhancedAction);
  acknowledgementNodes().forEach((node) => node.addEventListener("change", () => {
    actionError = null;
    renderAction();
  }));

  render();
  refresh();
})();
