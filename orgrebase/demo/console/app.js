const byId = (id) => document.getElementById(id);

const DEFAULT_LANGUAGE = "zh-CN";
const SUPPORTED_LANGUAGES = new Set(["zh-CN", "en"]);
const LANGUAGE_STORAGE_KEY = "orgrebase.console.language";
const PRESENTATION_QUERY_KEY = "presentation";
const READABLE_PRESENTATION_MODE = "readable";
const COMPACT_PRESENTATION_MODE = "compact";

function applyPresentationMode(url = document.URL) {
  const presentation = new URL(url).searchParams.get(PRESENTATION_QUERY_KEY);
  if (presentation === COMPACT_PRESENTATION_MODE) {
    document.documentElement.removeAttribute("data-presentation");
    return;
  }
  document.documentElement.dataset.presentation = READABLE_PRESENTATION_MODE;
}

applyPresentationMode();

// UI copy is deliberately centralized. Runtime identifiers and evidence payloads
// remain byte-for-byte authoritative; this dictionary only controls presentation.
const I18N = Object.freeze({
  "zh-CN": Object.freeze({
    "meta.title": "OrgRebase｜知变 · 企业工作持续演化引擎",
    "a11y.home": "OrgRebase 首页",
    "a11y.runtimeBoundary": "运行边界",
    "a11y.currentOperator": "当前阶段执行方",
    "a11y.complexityBridge": "原流程难点与 OrgRebase 解法",
    "a11y.currentAction": "当前合法操作",
    "a11y.evidenceViews": "执行视图",
    "a11y.activeEvidenceSpine": "本次运行链路",
    "a11y.activeTopology": "当前报价业务智能体有向拓扑",
    "a11y.retainedEvidence": "已验证的动态组队与控制语义",
    "a11y.retainedTopology": "保留的 AgentTeams 有向拓扑",
    "a11y.formationTopology": "两领域动态组队 AgentTeams 拓扑",
    "a11y.oacAdaptationStages": "OAC 企业接入六阶段",
    "a11y.oacReviewOverview": "契约审阅概览",
    "a11y.oacReviewComponents": "五类企业材料如何形成契约",
    "a11y.oacAdaptationOutcomes": "OAC 企业适配结果",
    "a11y.oacSourceBindings": "五类企业输入的技术绑定",
    "language.group": "语言",
    "language.switchChinese": "切换为中文",
    "language.switchEnglish": "切换为英文",
    "brand.tagline": "企业工作持续演化引擎",
    "hero.title": "事实变了，工作应该知道哪里失效。",
    "hero.body": "企业规则变化后，OrgRebase识别受影响的工作，组织领域智能体生成修正候选。指定负责人批准后，控制面更新正式版本。当前支持企业报价流程。",
    "operator.label": "当前阶段执行方",
    "operator.name": "报价运营负责人",
    "operator.role": "报价运营负责人",
    "complexity.original.kicker": "01 · 原流程",
    "complexity.original.title": "一次变化，穿过五类系统与五类责任人",
    "complexity.original.body": "CRM / CPQ、产品源、CLM、企业资源计划（ERP）/ 定价系统与市场和商业化组装之间，事实、权限和版本彼此分离。",
    "complexity.engineering.kicker": "02 · 工程难点",
    "complexity.engineering.title": "算准影响，又不让智能体越权写入",
    "complexity.engineering.body": "依赖必须可证明，候选与规范状态必须隔离，冲突、陈旧批准与中断必须能恢复。",
    "complexity.solution.title": "组织契约与选择性重构把一次变化收敛为可验证闭环",
    "complexity.solution.body": "自动形成与预演 → 人工审阅并点击 → 自动选择性重构与证据校验。",
    "journey.title": "一条持久化、可审计的演化时间线",
    "journey.loading": "正在读取服务端状态…",
    "journey.new": "新工作事项 · 等待生成内部工作稿",
    "journey.restored": "已从服务端恢复 {stage} · 刷新不丢状态",
    "journey.unavailable": "工作区服务不可用",
    "timeline.quoteV1": "已准入报价基线",
    "timeline.quoteV1.detail": "人工启动基线 · 智能体自动形成候选",
    "timeline.launch.title": "产品日期变更",
    "timeline.preview.detail": "上游变化到达 · 系统执行零写预演",
    "timeline.approve.detail": "人工 · 批准本次变更",
    "timeline.rebase.detail": "自动 · 应用已批准变更",
    "timeline.quoteV2": "上线日期确认稿",
    "timeline.recovery.title": "服务恢复点",
    "timeline.recovery.detail": "自动 · 持久化恢复",
    "timeline.currency.title": "财务币种变更",
    "event.preview": "计算本次变化的影响",
    "event.previewDetail": "核对本次事件及当前成果的依赖，生成可审批预演。",
    "event.approve": "批准本次变更",
    "event.approveDetail": "负责人确认这份预演，批准绑定到精确内容与版本。",
    "event.apply": "应用已批准变更",
    "event.applyDetail": "再次核对来源、审批和成果版本，原子提交修订。",
    "event.recover": "恢复结果记录",
    "event.recoverDetail": "核对已经提交的变更并补齐原结果记录，不重复变更或重新批准。",
    "event.reject": "拒绝本次变更",
  "event.rejectionReason": "拒绝原因",
  "action.openChange.label": "查看本次提案",
  "event.confirmReject": "记录拒绝",
  "event.rejected": "负责人已拒绝；修改后请提交新的变更。",
  "event.reasonRequired": "请填写拒绝原因。",
  "event.idle": "等待下一次变化",
    "event.idleDetail": "当前成果已保存，可继续接收新的业务事件。",
    "event.current": "当前成果",
    "event.previewed": "变更待审批",
    "event.evidenceRequired": "变更待补证",
    "event.approved": "变更已批准",
    "event.recoveryRequired": "变更已生效，待恢复结果记录",
    "timeline.quoteV3": "币种确认稿",
    "timeline.quoteV3.detail": "自动 · 内部变更证据校验",
    "command.next": "下一步",
    "command.loadingTitle": "读取工作区状态",
    "command.loadingDetail": "页面只显示当前阶段合法的主操作。",
    "command.loadingButton": "请稍候…",
    "command.unavailableTitle": "工作区状态不可用",
    "command.unavailableDetail": "未读取到权威状态；页面不会把失败伪装成空工作区，也不会开放业务操作。",
    "command.unavailableButton": "等待服务恢复",
    "command.unavailableActor": "工作区状态服务",
    "command.unavailableRole": "默认拒绝 · 保留最后一次已验证状态",
    "command.downloadQuote": "下载工作结果",
    "command.downloadEvidence": "下载审计记录",
    "command.reviewWait": "服务端四秒审阅门尚未到时；还需 {seconds} 秒。倒计时结束后，人工批准按钮才会解锁。",
    "command.reviewButton": "请审阅预演 · {seconds} 秒",
    "command.approveButton": "人工确认批准",
    "command.approveLaunchButton": "批准产品日期变更",
    "command.approveCurrencyButton": "批准财务币种变更",
    "command.completed": "已校验 · 内部变更闭环完成",
    "command.baseline": "报价基线已形成 · 尚无业务规则变更",
    "command.recorded": "结果已记录 · 正在核对完成回执",
    "header.currentTask.baseline": "当前任务：报价已生成",
    "currentArchive.titleBaseline": "本次报价基线已归档",
    "currentArchive.baselineArchived": "已归档 · 尚无规则变更批准或Rebase",
    "value.current.baseline.title": "当前报价基线",
    "value.current.baseline.detail": "报价已生成，暂无待审批的规则变更。需要调整规则时，请先查看影响并提交负责人审批。",
    "value.current.baseline.noChanges": "尚无规则变更决策",
    "command.ownerReview": "人工 · 负责人审阅 · {seconds} 秒",
    "command.ownerAction": "人工 · 等待负责人操作",
    "command.humanStart": "人工启动基线 · 变化负责人",
    "command.autoControl": "自动 · 确定性控制面",
    "command.oacGate": "控制面判定 · OAC 业务启动门",
    "command.oacChecking": "正在校验 · OAC 业务启动门",
    "command.experienceReview": "人工 · 经验候选审阅",
    "command.formRunning": "智能体自动执行 · 尚无终态",
    "command.formRunning.title": "固定版本 AgentTeams 正在形成内部工作稿候选",
    "command.formRunning.detail": "预期协议：领域智能体只提交候选，审查智能体验收后，OrgRebase 控制面才可形成协作工作稿；当前尚未观察到终态。",
    "command.formRunning.actor": "固定版本 AgentTeams + OrgRebase 控制面",
    "command.formRunning.role": "候选编排 / 终态验收 · 规范写入仍未发生",
    "command.formRunning.button": "正在形成协作工作稿…",
    "cockpit.title": "企业执行观测中枢",
    "cockpit.body": "业务、协作与运维共用同一运行链路；页面只读展示实际进度，不改写业务状态。",
    "cockpit.tab.scene": "业务与数据",
    "cockpit.tab.value": "价值与责任",
    "cockpit.tab.collaboration": "智能体协作",
    "cockpit.tab.operations": "运维与异常",
    "dataJourney.aria": "报价任务四段版本链",
    "dataJourney.boundaries.aria": "证据边界",
    "dataJourney.kicker": "本次任务 · 变更与验收",
    "dataJourney.title": "变化影响与报价后继版本",
    "dataJourney.waiting": "等待已准入的运行事实",
    "dataJourney.status.ready": "{facts} 条企业事实 → {slots} 个任务槽位 → {version} · 只读视图，智能体候选层业务写入 0",
    "dataJourney.status.progress": "当前运行正在形成数据链；只展示已观测事实，智能体候选层业务写入 {writes}",
    "dataJourney.status.waiting": "运行事实尚未齐备 · 证据不足时保持关闭 · 智能体候选层业务写入 {writes}",
    "dataJourney.source.title": "已准入企业基线",
    "dataJourney.source.waiting": "尚未观测到已准入业务槽位",
    "dataJourney.source.summary": "{count} 条已准入事实 · {components} 类 OAC 企业材料",
    "dataJourney.source.value": "当前值",
    "dataJourney.source.provenance": "来源 {source} · 权威 {authority}",
    "dataJourney.source.capabilityRequirement": "企业能力要求引用 · 不是本次装载的 Skill 包",
    "dataJourney.source.unbound": "来源或权威引用未观测",
    "dataJourney.source.registeredReference": "已登记业务来源",
    "dataJourney.source.productCatalog": "产品目录",
    "dataJourney.source.releasePlan": "发布计划",
    "dataJourney.source.platformCapability": "平台能力源",
    "dataJourney.source.contractRegister": "合同登记簿",
    "dataJourney.source.pricingPolicy": "定价政策",
    "dataJourney.source.currencyPolicy": "币种政策",
    "dataJourney.source.partnerPolicy": "合作政策",
    "dataJourney.source.skillRegistry": "Skill 注册表",
    "dataJourney.source.publicMessage": "对外口径源",
    "dataJourney.authority.product": "产品负责人",
    "dataJourney.authority.legal": "法务负责人",
    "dataJourney.authority.finance": "财务负责人",
    "dataJourney.authority.gtm": "市场与商业化负责人",
    "dataJourney.authority.skillRegistry": "Skill 注册表负责人",
    "dataJourney.oac.title": "OAC 约束已绑定",
    "dataJourney.oac.waiting": "尚未观测到完整契约绑定",
    "dataJourney.oac.summary": "{count}/5 类契约已绑定 · 来源准入 {admission} · 激活投影 {projection}",
    "dataJourney.oac.summaryBound": "{count}/5 类契约已绑定 · OAC 已形成 {domains} 个领域任务 · 执行拓扑一致",
    "dataJourney.oac.summaryPending": "{count}/5 类来源已准入 · 组织契约尚待激活",
    "dataJourney.oac.summaryActivated": "{count}/5 类来源已准入 · 契约已激活并被任务形成消费 · 执行绑定待证明",
    "dataJourney.oac.binding": "来源 {admission} · 投影 {projection}",
    "dataJourney.oac.bindingPending": "来源 {admission} · 投影 {projection} · 精确绑定待补齐",
    "dataJourney.oac.activation": "报价前置绑定 {status}",
    "dataJourney.oac.boundary": "OAC 负责准入组织材料并约束本次任务与最小上下文；AgentTeams 只执行候选任务，OrgRebase 控制面负责验收和规范写入。",
    "dataJourney.oac.boundaryPending": "组织契约尚未与本次 AgentTeams 执行计划形成同运行绑定；当前只展示已观测的来源准入和激活状态。",
    "dataJourney.context.title": "OrgRebase 最小上下文",
    "dataJourney.context.waiting": "尚未观测到任务投影",
    "dataJourney.context.summary": "{domains} 个领域智能体 · {actors} 个受控接收者 · {slots} 个任务槽位",
    "dataJourney.context.actor": "收到 {included} 个必需槽位 · 排除 {excluded} 个槽位",
    "dataJourney.context.reasons": "记录的排除原因：{reasons}",
    "dataJourney.context.reason.otherDomain": "其他职责域 {count} 项",
    "dataJourney.context.reason.derivedOnly": "只允许派生信息 {count} 项",
    "dataJourney.context.reason.forbidden": "禁止输出 {count} 项",
    "dataJourney.context.reason.sensitivity": "超过敏感度上限 {count} 项",
    "dataJourney.context.reason.notRequired": "本任务不需要 {count} 项",
    "dataJourney.context.reason.other": "其他原因 {count} 项",
    "dataJourney.context.noSlots": "未向该接收者投影业务槽位",
    "dataJourney.context.authority": "形成权威：OrgRebase 控制面任务上下文编译器",
    "dataJourney.quotes.title": "报价版本链",
    "dataJourney.quotes.waiting": "尚未形成内部报价修订",
    "dataJourney.quotes.summary": "{count} 个已观测修订 · 当前 {version}",
    "dataJourney.quotes.version": "{version} · 上线 {launch} · 币种 {currency}",
    "dataJourney.quotes.incomplete": "修订历史不完整，不补造缺失修订",
    "dataJourney.changes.title": "审批前候选影响与审批后选择性重构",
    "dataJourney.changes.waiting": "尚未观测到业务变化，不补造变更或批准。",
    "dataJourney.changes.launchDate": "产品上线日期",
    "dataJourney.changes.currency": "报价币种",
    "dataJourney.changes.delta": "{from} → {to}",
    "dataJourney.changes.owner": "人工负责人 {owner} · 批准 {approval}",
    "dataJourney.changes.transition": "{before} → {after} · 回执 {receipt}",
    "dataJourney.changes.metrics": "重建 {rebuilt} · 证据保留 {preserved} · 人工复核 {unknown} · 错误作废 {falseInvalidations} · 越权泄露 {unauthorized}",
    "dataJourney.changes.notObserved": "本轮回执未完整观测，不展示成已完成。",
    "dataJourney.changes.previewObserved": "VMRC 已精确绑定零写预演；下列为审批前候选影响，不是已应用结果。",
    "dataJourney.changes.previewMetrics": "零写候选：重建 {rebuilt} · 证据保留 {preserved} · 人工复核 {unknown}",
    "dataJourney.changes.previewOwner": "人工负责人 {owner} · 待批准（规范写入仍为 0）",
    "dataJourney.changes.previewTransition": "当前版本保持 {before} · 批准前不生成后继版本",
    "dataJourney.boundary": "前两段是已准入基线。变化到达后先显示零写预演与候选影响；只有匹配负责人批准后，才显示后继报价版本和选择性重构回执。",
    "dataJourney.boundaries.fixture": "受控实例：证明结构闭环",
    "dataJourney.boundaries.public": "公开流程：独立验证跨流程映射",
    "dataJourney.boundaries.pilot": "企业试点：真实连接器与 ROI 需在企业环境验证",
    "dataJourney.component.domain": "领域",
    "dataJourney.component.knowledge": "知识",
    "dataJourney.component.authority": "权威",
    "dataJourney.component.capability": "能力",
    "dataJourney.component.dependency": "依赖",
    "dataJourney.slot.productPlan": "企业方案",
    "dataJourney.slot.launchDate": "上线日期",
    "dataJourney.slot.dataResidency": "数据驻留",
    "dataJourney.slot.noticeRequired": "客户通知要求",
    "dataJourney.slot.priceBand": "价格带",
    "dataJourney.slot.pricingPolicy": "报价计算规则",
    "dataJourney.slot.quoteBasket": "报价商品明细",
    "dataJourney.slot.currency": "币种",
    "dataJourney.slot.partnerTerms": "合作条款",
    "dataJourney.slot.quoteComposeSkill": "报价组装 Skill",
    "dataJourney.slot.publicMessage": "对外口径",
    "dataJourney.value.approvedSchedule": "企业版本上线计划保持在已批准时间表内。",
    "dataJourney.sensitivity.public": "公开",
    "dataJourney.sensitivity.internal": "内部",
    "dataJourney.sensitivity.confidential": "机密",
    "dataJourney.sensitivity.restricted": "受限",
    "dataJourney.actor.domain": "领域智能体",
    "dataJourney.actor.renderer": "确定性报价组装器",
    "dataJourney.actor.rendererRole": "确定性受控接收者",
    "dataJourney.verdict.admitted": "已准入",
    "dataJourney.verdict.match": "已匹配",
    "dataJourney.verdict.ready": "已就绪",
    "dataJourney.verdict.consumed": "已被报价形成前置消费",
    "dataJourney.verdict.notUsed": "本轮未使用",
    "dataJourney.verdict.notObserved": "未观测",
    "scene.waiting": "等待任务形成",
    "scene.waitingQuote": "等待报价形成",
    "scene.declaredProfile": "已声明配置档案",
    "scene.authority.detail": "智能体 / Tool / Skill 只能产生候选结果与审计记录",
    "scene.ledger.title": "本次运行操作台账",
    "scene.ledger.zero": "0 个操作",
    "scene.ledger.waiting": "等待形成运行事实…",
    "scene.ledger.count": "{count} 个操作 · 仅属本次运行",
    "businessChange.kicker": "审批前看候选影响 · 审批后看后继版本",
    "businessChange.title": "同一变化的预演与应用分开呈现",
    "businessChange.waiting": "等待变更运行",
    "businessChange.versionPath": "{path} · 当前工作状态：{stage}",
    "businessChange.round": "第 {round} 轮",
    "businessChange.launchDate": "产品上线日期",
    "businessChange.currency": "报价币种",
    "businessChange.agents": "协调器与领域智能体",
    "businessChange.owner": "人工负责人",
    "businessChange.reviewPassed": "审阅时间门已通过",
    "businessChange.reviewPending": "候选已自动生成 · 规范写入 0 · 等待匹配负责人",
    "businessChange.rebuilt": "重建",
    "businessChange.preserved": "证据保留",
    "businessChange.unknown": "转人工复核",
    "businessChange.falseInvalidations": "错误作废",
    "businessChange.unauthorized": "越权泄露",
    "businessChange.object.applied": "实际选择性重建",
    "businessChange.object.preserved": "明确未受影响",
    "businessChange.object.review": "证据不足·转人工",
    "businessChange.object.candidateRebuild": "候选重建",
    "businessChange.object.candidatePreserve": "候选证据保留",
    "businessChange.object.candidateReview": "候选转人工",
    "businessChange.object.change": "{object} · {from} → {to}",
    "businessChange.object.state": "{object} · {state}",
    "businessChange.object.none": "未观测到对象回执",
    "businessChange.totalTitle": "系统没有全量重做",
    "businessChange.totalSummary": "两轮合计：只重建 {rebuilt} 项，保留 {preserved} 项已有证据，{unknown} 项证据不足而停下等人。",
    "businessChange.totalSecurity": "错误作废 {falseInvalidations} · 越权泄露 {unauthorized} · 口径为两轮运行合计，不是去重后对象数。",
    "businessChange.progressSummary": "{previewed}/2 轮已形成 VMRC 候选影响，{completed}/2 轮已应用；不把预演冒充成完成回执。",
    "businessChange.previewObserved": "VMRC 已精确绑定零写预演；负责人批准后才能应用。",
    "businessChange.notObserved": "本轮收据尚未观测，不补造结果。",
    "businessChange.reviewAction": "返回当前负责人审批",
    "businessChange.archiveAction": "查看本次运行归档",
    "currentChange.kicker": "当前变更集 · 本地确定性智能体运行",
    "currentChange.title": "当前变化只投影最小团队，不重跑报价 v1 基线组队",
    "currentChange.waiting": "等待当前变更集预演",
    "currentChange.invalid": "变更集已存在，但运行、预演、负责人、交接或零写入绑定不完整；最小团队不予展示。",
    "currentChange.summary": "已观测 {rounds} 轮变更集 · 候选规范写入 {writes}",
    "currentChange.round": "第 {round} 轮 · {change}",
    "currentChange.team": "最小变更团队",
    "currentChange.owner": "精确匹配负责人",
    "currentChange.writes": "候选规范写入",
    "currentChange.agent": "智能体",
    "currentChange.candidate": "候选",
    "currentChange.capability": "智能体运行能力版本引用（非调用）",
    "currentChange.handoff": "交接",
    "currentChange.review": "确定性候选准入（非独立审查任务）",
    "currentChange.candidateValue": "{kind} · {digest}",
    "currentChange.capabilityValue": "Tool 引用 {tools} · Skill 引用 {skills}",
    "currentChange.handoffValue": "{from} → {to} · {digest}",
    "currentChange.reviewValue": "未新建独立审查任务 · {decision} · {verifier}",
    "currentChange.workValue": "工作：{purpose} · 范围 {scope} · 对象 {object}",
    "currentChange.noTool": "未记录外部 Tool（仅提议）",
    "currentChange.noSkill": "未记录 Skill 版本",
    "currentChange.noHandoff": "未观测交接回执",
    "currentChange.noReview": "未观测候选准入回执",
    "currentChange.boundary": "Tool / Skill 仅显示智能体运行记录的能力版本引用；Skill 引用不冒充已装载调用。当前变化轮未新建独立审查任务，验收结果来自确定性候选准入回执。全部候选规范写入为 0。",
    "authorityLadder.kicker": "初始形成与本次变更分别核对",
    "authorityLadder.changeTitle": "同一项变更，从提出到正式生效",
    "authorityLadder.phase.complete": "已记录",
    "authorityLadder.phase.current": "进行中",
    "authorityLadder.phase.waiting": "待证据",
    "authorityLadder.phase.blocked": "已停止",
    "authorityLadder.phase.unobserved": "未观察到",
    "businessValue.pricingPolicy": "整单折扣 {discount} · 税率 {tax} · {label}",
    "businessValue.quoteBasket": "{count} 项商品明细 · {currency}",
    "quotePricing.title": "报价金额明细",
    "quotePricing.caption": "当前生效版本的计算结果",
    "quotePricing.item": "商品 / 服务",
    "quotePricing.quantity": "数量",
    "quotePricing.unitPrice": "单价",
    "quotePricing.lineAmount": "行金额",
    "quotePricing.lineId": "明细 ID {id}",
    "quotePricing.subtotal": "商品小计",
    "quotePricing.discount": "折扣（{rate}）",
    "quotePricing.net": "折后净额",
    "quotePricing.tax": "税额",
    "quotePricing.total": "报价总额",
    "quotePricing.sources": "金额依据与计算口径",
    "quotePricing.basketSource": "明细来源",
    "quotePricing.policySource": "规则来源",
    "quotePricing.rounding": "舍入方式",
    "quotePricing.basketDigest": "明细摘要",
    "quotePricing.policyDigest": "规则摘要",
    "quotePricing.boundary": "金额由当前版本绑定的明细与规则计算；税费与折扣以所列规则来源为准。历史样本或受控演示规则不代表现时报价及历史实际税费。",
    "authorityLadder.title": "从团队交付到规则生效",
    "authorityLadder.at": "初始形成 · AgentTeams 已完成",
    "authorityLadder.candidate": "本次变更 · 候选已准入",
    "authorityLadder.reviewer": "初始形成 · 审查智能体已验收",
    "authorityLadder.human": "本次变更 · 人工已批准",
    "authorityLadder.canonical": "本次变更 · 规范状态已写入",
    "authorityLadder.atDetail": "{actions} 个 AgentTeams 原生操作 · 项目终态 {terminal}",
    "authorityLadder.candidateDetail": "控制面锁定本轮影响预演 · 候选层写入 0",
    "authorityLadder.reviewerDetail": "第 {attempt} 次复核 {verdict} · 确定性校验 · 目标写入 {writes}",
    "authorityLadder.humanDetail": "{owner} · 服务端审阅时间门通过",
    "authorityLadder.canonicalDetail": "本提案已原子提交 {quote}；当前报价 {current}",
    "authorityLadder.context": "工作区当前待办或最新提案：{event} · {status}。初始形成记录提供运行背景，不代替本提案的准入、批准或生效收据。",
    "authorityLadder.selectedContext": "所选提案：{event} · {status}。下列变更收据与上方所选提案一致；初始形成记录仅提供运行背景。",
    "authorityLadder.noChange": "暂无变更提案。提出规则调整后，可在此跟进审批与生效。",
    "authorityLadder.waiting": "未观测到本级独立收据",
    "authorityLadder.observed": "收据已观测",
    "technicalEvidence.title": "技术证据详情",
    "technicalEvidence.sceneSummary": "运行编号、候选链路与同运行操作台账",
    "table.type": "类型",
    "table.actor": "主体",
    "table.action": "做了什么",
    "table.status": "状态",
    "table.writes": "写入",
    "table.receipt": "回执",
    "value.boundary.initial": "八步流程用于现状人工基线与 RACI 对照；系统实测和人工等待必须分开读取。",
    "value.process.title": "八步报价责任链",
    "value.table.step": "步骤",
    "value.table.system": "现状系统类",
    "value.table.asIsResponsible": "现状人工执行者（R）",
    "value.table.toBeResponsible": "变化后执行者",
    "value.table.accountable": "人工批准责任（A）",
    "value.table.target": "参考人工基线",
    "value.table.connector": "连接器",
    "value.connector.controlledLocal": "受控本地数据源 / 工具",
    "value.connector.enterprisePending": "真实企业连接器未接入",
    "value.raci.legend": "现状 R 是当前人工执行者；变化后执行者是智能体或受控系统；A 仍是最终负责与批准的人。",
    "value.metrics.title": "本次任务验收",
    "value.metrics.boundary": "只展示当前运行已经产生并校验的回执",
    "value.cost.loading": "读取价值评估收据…",
    "value.primaryUnavailable": "证据不可用 · 不推断责任或价值",
    "value.boundary.valid": "当前验证切片：{deliverable}。表内时限是待企业校准的参考人工基线；系统实测来自运行回执，人工等待在审批门单独显示。",
    "value.boundary.invalid": "价值评估记录校验失败；未推断责任或价值。",
    "value.process.unavailable": "责任基线不可用；未补造。",
    "value.metric.unavailable": "未推断、未补造。",
    "value.cost.summary": "{full} 全量 → {selective} 选择性 → {saving} 模型化节省",
    "value.cost.unavailable": "价值收据不可用",
    "value.current.waiting.label": "本次运行",
    "value.current.waiting.value": "等待任务完成",
    "value.current.waiting.detail": "结果将在本次运行形成并校验后显示",
    "value.current.waiting.evidence": "当前阶段 {stage} · 不使用历史基线填充",
    "value.current.invalid.value": "结果暂不可用",
    "value.current.invalid.detail": "运行已到终态，但同运行回执绑定未通过校验",
    "value.current.invalid.evidence": "校验未通过 · 不回退显示冻结数值",
    "value.current.metric.quote": "最终内部修订",
    "value.current.metric.quote.detail": "当前内部修订与最终回执绑定",
    "value.current.metric.receipts": "变更回执",
    "value.current.metric.receipts.detail": "已完成的选择性重构均属于当前运行",
    "value.current.metric.approvals": "负责人批准",
    "value.current.metric.approvals.detail": "归档中已核验的实际来源范围批准数",
    "value.current.summary.detail": "完整归档已校验；本页只加载近期变化，未合计工作项级细项，也未换算工时、金额或 ROI。",
    "value.current.summary.label": "工作项细项未计算",
    "value.current.metric.decisions": "影响决策",
    "value.current.metric.decisions.detail": "已应用变更的目标决策合计",
    "value.current.metric.rebuilt": "实际重建",
    "value.current.metric.rebuilt.detail": "只重建被证明受影响的工作项",
    "value.current.metric.preserved": "证据保留",
    "value.current.metric.preserved.detail": "在声明边界内继续有效",
    "value.current.metric.held": "转人工复核",
    "value.current.metric.held.detail": "证据不足时停手，不猜测",
    "value.current.metric.safety": "安全异常",
    "value.current.metric.safety.detail": "错误作废 {falseInvalidations} · 越权泄露 {unauthorized}",
    "value.current.metric.evidence": "当前运行 · 已校验回执",
    "value.current.cost.label": "本次运行结构性结果",
    "value.current.cost.summary": "{decisions} 项影响决策 → {rebuilt} 项实际重建",
    "value.current.cost.detail": "实际回执：保留 {preserved} · 转人工 {unknown} · 错误作废 {falseInvalidations} · 越权泄露 {unauthorized}<br>未换算为金额、工时或企业 ROI",
    "value.current.cost.modelledLabel": "完成态模型化成本对照",
    "value.current.cost.modelledSummary": "{full} 全量 → {selective} 选择性 → {saving} 模型化节省",
    "value.current.cost.modelledDetail": "声明区间 {lower}–{upper} · 压力区间 {stressLower}–{stressUpper}<br>来自独立校验的价值收据 · 归一化操作成本 · 不是本次实测、工时或金额 · 非真实企业 ROI",
    "value.current.cost.waiting": "等待本次任务运行完成",
    "value.current.cost.waitingDetail": "历史成本模型不作为本次结果；周期、工时、返工率与 ROI 需企业观察窗口。",
    "proofOverview.kicker": "历史发布验证",
    "proofOverview.title": "按能力查看验证结果",
    "proofOverview.boundary": "以下是独立运行的历史记录；当前任务见上方运行档案。",
    "proofOverview.loading": "正在校验证据…",
    "proofOverview.pass": "已校验",
    "proofOverview.historical": "历史已校验 · 非当前构建资格",
    "proofOverview.historicalBuild": "保留的是历史制品的验证结果；未据此重新构建或认证当前版本。",
    "proofOverview.unavailable": "证据不可用",
    "proofOverview.waitingOac": "等待本次企业接入准入",
    "proofOverview.golden.title": "报价流程 · 历史运行",
    "proofOverview.golden.detail": "OAC → AgentTeams → Tool / Skill → 载荷中已验证的人工门 → 选择性重构 → 完成态复核",
    "proofOverview.oac.title": "OAC 企业适配",
    "proofOverview.oac.detail": "已声明材料、未知项、负责人准入与精确激活绑定",
    "proofOverview.bpi.title": "公开真实流程",
    "proofOverview.bpi.detail": "BPI 2019 真实匿名采购到付款日志上的作用域复验",
    "proofOverview.formation.title": "按需动态组队",
    "proofOverview.formation.detail": "不同需求形成不同领域智能体与审查智能体拓扑",
    "proofOverview.agentic.title": "智能体候选适配",
    "proofOverview.agentic.detail": "智能体提议 BPI→OAC 映射，确定性控制保留权限与缺口",
    "proofOverview.product.kicker": "产品路径回归验证 · 不是第六条业务链",
    "proofOverview.product.title": "真实 HTTP 入口、攻击变体与重启重放",
    "proofOverview.product.loading": "正在读取当前产品路径验证收据…",
    "proofOverview.product.detail": "{casesPassed}/{caseCount} 路径 · {mutationsKilled}/{mutationCount} 变体 · {integrityRejected}/{integrityTotal} 完整性攻击 · {gateRejected}/{gateTotal} 准入攻击 · {processes} 个真实服务进程 · {restarts} 次真实重启",
    "currentArchive.kicker": "本次运行档案 · 完成态",
    "currentArchive.title": "任务完成后，在此查看运行记录",
    "currentArchive.titleReady": "任务已归档",
    "currentArchive.description": "查看本任务的团队执行、人工批准、版本更新和交付结果。",
    "currentArchive.pending": "等待当前任务完成",
    "currentArchive.loading": "正在校验同运行完成态回执…",
    "currentArchive.archived": "已归档",
    "currentArchive.invalid": "档案闭合校验未通过 · 保持关闭",
    "currentArchive.unavailable": "完成态档案不可用",
    "currentArchive.retry": "重新读取证据",
    "currentArchive.retryBoundary": "只重新读取当前运行未能取得的档案或可观测证据，不重跑任务、不重发审批。",
    "currentArchive.runId": "本次运行 run_id",
    "currentArchive.sameRun": "与刚完成的报价任务相同",
    "currentArchive.finalQuote": "最终交付",
    "currentArchive.receipts": "审批与更新",
    "currentArchive.receiptsValue": "{approvals} 次人工批准 · {rebases} 次选择性重构",
    "currentArchive.receiptsDetail": "本任务的完成记录",
    "currentArchive.authority": "记录维护方",
    "currentArchive.authorityDetail": "智能体 / Tool / Skill 都不能写入本档案结论",
    "currentArchive.fields": "上线日期 {launch} · 币种 {currency}",
    "currentArchive.boundary": "归档前校验任务身份、批准依据、版本与事件链。历史验证记录单独保存。",
    "archive.frozen.kicker": "可靠性验证 · 历史记录",
    "archive.frozen.title": "历史验证记录",
    "archive.frozen.boundary": "按原始运行身份查看结果和来源；与当前任务记录分开保存。",
    "publicValidation.kicker": "公开数据案例 · BPI 2019",
    "publicValidation.title": "采购规则变化会影响哪些订单？",
    "publicValidation.loading": "读取收据…",
    "publicValidation.runId": "公开流程机制验证运行",
    "publicValidation.runSummary": "独立 run_id · {runId}",
    "publicValidation.archiveStatus": "档案状态",
    "publicValidation.archiveFrozen": "已冻结 · 非当前报价任务",
    "publicValidation.archiveTime": "原始档案摘要",
    "publicValidation.adaptationRunId": "OAC 候选适配运行",
    "publicValidation.executionRunId": "类型化作用域执行运行",
    "publicValidation.pass": "独立复验通过",
    "publicValidation.unavailable": "尚未生成",
    "publicValidation.fail": "收据校验失败",
    "publicValidation.description": "BPI 2019真实匿名采购日志案例：比较规则变化的影响范围与来源依据。",
    "publicValidation.descriptionWithSource": "{source}；采购规则变化的范围分析案例。",
    "publicValidation.method": "官方真实日志 → 摘要固定投影 → OAC 类型化作用域 → 独立离线复验",
    "publicValidation.adaptation.aria": "BPI 2019 公开真实采购到付款机制的 OAC 适配附加验证",
    "publicValidation.adaptation.kicker": "BPI → OAC 附加验证",
    "publicValidation.adaptation.title": "智能体候选映射与确定性准入",
    "publicValidation.adaptation.runBoundary": "候选适配与作用域执行是两条独立运行；不与 BPI 范围基准混成同一 run_id。",
    "publicValidation.adaptation.runRelation": "运行关系",
    "publicValidation.adaptation.runRelationValue": "分离留证 · 不合并运行编号",
    "publicValidation.adaptation.method": "公开真实日志 → 受控 AgentTeams 候选映射 → 确定性 OAC 校验 → 负责人审阅门 → 128 查询执行 → 独立复验",
    "publicValidation.adaptation.mapper": "候选映射",
    "publicValidation.adaptation.mapperValue": "{model} · {provider} · {evidence} · AgentTeams {count} 个原生动作",
    "publicValidation.adaptation.validator": "确定性 OAC 校验",
    "publicValidation.adaptation.validatorPass": "通过 · 候选无写入权",
    "publicValidation.adaptation.unknowns": "企业事实缺口",
    "publicValidation.adaptation.unknownValue": "{count} 项必须由企业声明：{dimensions}",
    "publicValidation.adaptation.unknownApprovalAuthorities": "批准权威",
    "publicValidation.adaptation.unknownOrganizationValues": "组织价值",
    "publicValidation.adaptation.unknownPermissions": "权限",
    "publicValidation.adaptation.unknownResponsibleOwners": "真实负责人",
    "publicValidation.adaptation.liveModel": "真实模型请求已观测",
    "publicValidation.adaptation.gate": "受控本地审批等待门",
    "publicValidation.adaptation.gateValue": "提前脚本指令已拒绝 · {elapsed} 秒后准入",
    "publicValidation.adaptation.queries": "类型化作用域执行",
    "publicValidation.adaptation.queryValue": "{count} 个固定查询",
    "publicValidation.adaptation.recall": "独立规则复验召回（不是模型准确率）",
    "publicValidation.adaptation.falseUnaffected": "错误判为无影响",
    "publicValidation.adaptation.writes": "规范状态写入",
    "publicValidation.adaptation.boundary": "BPI 采购到付款附加验证：真实公开日志可由智能体生成受限 OAC 候选并驱动类型化作用域复验；不等于报价数据、企业 ROI、生产部署或“自动适配任意企业”。负责人门是受控本地命令，未证明外部真人验收。",
    "publicValidation.adaptation.unavailable": "候选映射记录暂不可用，请检查证据包完整性。",
    "publicValidation.boundaryAdapted": "公开真实日志到智能体候选映射、确定性 OAC 校验和 128 查询复验已闭合。任务规则判定基准由查询合同派生，是非人工因果标注、非报价数据、非 ROI 证据。",
    "publicValidation.unavailableDescription": "此代码包未包含可读取的BPI采购流程记录。",
    "publicValidation.failDescription": "记录完整性检查未通过，请检查或重新安装证据包。",
    "publicValidation.traces": "采购订单项目轨迹",
    "publicValidation.events": "事件",
    "publicValidation.activities": "活动类型",
    "publicValidation.queries": "固定价格变更查询",
    "publicValidation.scopeAria": "影响作用域安全收缩",
    "publicValidation.scope.vendor": "供应商级广播",
    "publicValidation.scope.document": "采购单级约束",
    "publicValidation.scope.oac": "OAC 类型化作用域（机制验证）",
    "publicValidation.scope.baseline": "对照作用域",
    "publicValidation.scope.reduction": "安全收缩 {value}",
    "publicValidation.recall": "查询合同召回",
    "publicValidation.unsafe": "错误判为无影响",
    "publicValidation.lineage": "来源链闭合",
    "publicValidation.selectionWaiting": "读取样本选择与任务规则判定基准…",
    "publicValidation.selection": "{eligible} 个合格查询 → 摘要固定选择 {selected} 个 · 任务规则判定基准：由查询合同派生，对被测策略不可见",
    "publicValidation.boundary": "已验证类型化范围、保守停手和来源血缘；尚未证明 BPI 数据经智能体自动生成 OAC 并绑定 OrgRebase 运行。任务规则判定基准是非人工因果标注、非报价数据、非 ROI 证据。",
    "publicValidation.details": "数据来源、许可与验证范围",
    "publicValidation.source": "官方数据源",
    "publicValidation.license": "许可",
    "publicValidation.rawHash": "原始文件 SHA-256",
    "publicValidation.receipt": "基准回执",
    "publicValidation.verification": "独立复验回执",
    "publicValidation.redistribution": "原始 XES 不随代码包分发；交付包保留可复现的固定投影、完整性校验与 CC BY 4.0 归属。",
    "process.intake": "需求接收与入场",
    "process.product": "产品能力与上线确认",
    "process.legal": "法务义务确认",
    "process.finance": "定价与币种政策确认",
    "process.compose": "报价组装与证据绑定",
    "process.accept": "对客交付验收",
    "process.impact": "变化影响预演",
    "process.update": "批准后选择性更新",
    "metric.cycle": "报价周期",
    "metric.policy": "政策确认主动工时",
    "metric.mismatch": "完整影响不匹配样例率",
    "metric.rework": "首次交付返工率",
    "metric.access": "越权访问成功率",
    "metric.review": "待审决策率（升级代理）",
    "metric.saving": "选择性重构成本节省",
    "metric.detail.mismatch": "{numerator}/{denominator} 个受控演示样例 · 不是真实漏改率",
    "metric.detail.security": "{numerator}/{denominator} 个受控安全样例",
    "metric.detail.review": "{numerator}/{denominator} 个本地决策 · 不是人力基线",
    "metric.detail.cost": "{full} → {selective} 归一化成本 · 不是 ROI",
    "metric.detail.noShadow": "尚无同一企业的影子验证观测窗口",
    "collaboration.waiting.title": "等待启动变化驱动案例",
    "collaboration.waiting.detail": "形成报价后，这里会展示同一运行的控制面任务协调器、领域智能体、审查智能体，以及智能体调用的 Tool / Skill 和控制面记录。",
    "collaboration.empty.kicker": "尚未组队",
    "collaboration.empty.title": "任务准入后才生成本次智能体团队",
    "collaboration.empty.detail": "完成 OAC 准入并确认员工任务后，系统才会生成本次执行计划、领域智能体与最小上下文。",
    "collaboration.local.active.title": "当前报价运行 · 受控协作进行中",
    "collaboration.local.active.detail": "本次运行已形成最小上下文与候选控制记录；尚未绑定本次运行的 AgentTeams 原生生命周期或独立审查智能体收据。下方独立验证不得借给本次运行。",
    "collaboration.local.complete.title": "任务已完成 · 查看协作记录",
    "collaboration.local.complete.detail": "查看各领域候选、负责人决定和版本更新；其他运行的验证结果位于“运行记录与验证”。",
    "collaboration.active.title": "报价 v1 基线组队回看｜非当前变更集重跑",
    "collaboration.active.detail": "报价 v1 的 AgentTeams 组队、财务补证与报价组装 Skill 属于初始形成过程。后续变化按影响领域组织候选任务；是否实际执行原生 AT 任务，请查看该变更的执行记录。",
    "collaboration.active.model": "{suggestion} · 已冻结回执；只供审查智能体参考，确定性校验器拥有验收权威。",
    "agentWork.kicker": "报价 v1 · 初始组队记录",
    "agentWork.title": "逐个查看执行主体的输入、调用与输出",
    "agentWork.waiting": "等待同运行智能体回执",
    "agentWork.controlsAria": "智能体工作明细展开控制",
    "agentWork.expandAll": "展开全部",
    "agentWork.collapseAll": "收起全部",
    "agentWork.boundary": "智能体提交候选，Reviewer复核，指定负责人批准；正式版本由控制面更新。",
    "agentWork.viewEvolution": "继续查看业务数据演化",
    "agentWork.summary": "{actors} 个执行主体 · {tasks} 个 AgentTeams 原生任务 · {activities} 条同运行活动",
    "agentWork.fact.agents": "执行主体",
    "agentWork.fact.tasks": "原生任务",
    "agentWork.fact.writes": "智能体 / Tool / Skill 规范写入",
    "agentWork.fact.value": "{count} 个",
    "agentWork.fact.zeroWrites": "0 次",
    "agentWork.card.activities": "{count} 条同运行活动",
    "agentWork.field.task": "负责什么",
    "agentWork.field.context": "收到的最小上下文",
    "agentWork.field.capability": "实际调用的 Tool / Skill / 运行能力",
    "agentWork.field.output": "产出的候选或审查结果",
    "agentWork.field.handoff": "交接到",
    "agentWork.field.effect": "最终与哪些业务对象有关",
    "agentWork.field.timeline": "回执时间线",
    "agentWork.context": "收到 {included} 个必需字段，排除 {excluded} 个字段：{slots}",
    "agentWork.context.control": "只收到受控任务图、候选摘要与回执引用",
    "agentWork.noCapability": "未观测到该智能体调用 Tool 或 Skill",
    "agentWork.noLoadedSkill": "未调用装载型 Skill（不从角色配置推断）",
    "agentWork.noCandidate": "未观测到候选业务字段",
    "agentWork.candidateUnavailable": "未观测到可与原生任务、交接和进程回执交叉校验的候选摘要",
    "agentWork.candidateVerified": "已验证候选摘要 · 独立进程输出与任务、交接及回执摘要一致",
    "agentWork.candidateBoundary": "候选输出 · 尚未生效",
    "agentWork.candidateAttempt": "A{attempt} · {status}",
    "agentWork.candidateProvenance": "来源 {source} · 候选版本 {version}",
    "agentWork.candidateMissing": "本轮缺失：{fields}",
    "agentWork.candidateReasons": "理由：{reasons}",
    "agentWork.candidateTransformation": "最小派生：{transformation}",
    "agentWork.reviewVerdict": "审查结论 {verdict}",
    "agentWork.reviewMissing": "缺口领域：{domains}",
    "agentWork.executorRef": "执行器 {executor}",
    "agentWork.capability.businessRecoveryTool": "业务补证 Tool",
    "agentWork.capability.businessRecoveryTool.detail": "财务 A2 独立执行智能体通过受控本地真实 HTTP 补齐价格带",
    "agentWork.capability.businessRecoveryBinding": "业务补证运行绑定",
    "agentWork.capability.postFormationAuditTool": "形成后依赖审计 Tool",
    "agentWork.capability.postFormationAuditTool.detail": "报价 v1 提交时由工作区服务读取已形成的只读依赖图；市场与商业化执行者仅是回执主体，不代表独立进程自主调用",
    "agentWork.capability.loadedSkill": "实际装载 / 评测 / 调用的 Skill 包",
    "agentWork.capability.advisorySkillRef": "变更建议 Skill 引用 · 非装载包调用",
    "agentWork.capability.runtime": "AgentTeams 运行能力",
    "agentWork.candidateOnly": "候选权限 · 无规范状态写入权",
    "agentWork.taskReceipt": "{task} · 尝试 {attempt} · {status}",
    "agentWork.reviewProcess": "已记录独立执行进程",
    "agentWork.modelAdvice.true": "模型建议已采纳",
    "agentWork.modelAdvice.false": "模型建议未采纳",
    "agentWork.review.model": "模型建议",
    "agentWork.review.deterministic": "确定性复核",
    "agentWork.review.unknown": "未记录，无法判断",
    "agentWork.review.missing": "缺口领域：{domains}",
    "agentWork.review.none": "无",
    "agentWork.review.authority": "模型提供建议，确定性复核决定候选是否可用。",
    "agentWork.reviewProcessBoundary": "人工批准在变更审批中单独记录。",
    "agentWork.managerReceipt": "{task} · 协调运行 · {status}",
    "agentWork.handoffReceipt": "{kind} → {target} · 候选写入 {writes}",
    "agentWork.timelineReceipt": "#{sequence} · {plane} · {status}",
    "agentWork.timelineEvidence": "回执 {receipt}",
    "agentWork.relatedObjects": "本运行相关对象：{objects}",
    "agentWork.task.coordinator": "按 OAC 已冻结计划组织领域任务，并在证据不足时生成第二版任务图",
    "agentWork.task.product": "核对企业方案、上线日期与数据驻留候选事实",
    "agentWork.task.legal": "核对客户通知要求，只交付最小法务结论",
    "agentWork.task.finance": "核对币种和价格带；首轮缺失时主动放弃，补证后再交付",
    "agentWork.task.gtm": "核对合作条款与报价能力，验收通过后组装报价候选",
    "agentWork.task.reviewer": "汇总四域候选，独立检查完整性、来源与权限边界",
    "agentWork.output.coordinator": "任务依赖图与重新规划记录",
    "agentWork.output.reviewer": "第一轮重新规划：财务价格带缺失；第二轮验收通过：精确字段来源与作用边界通过",
    "agentWork.effect.coordinator": "只调度与重规划，不产生业务结论",
    "agentWork.effect.product": "提供当前任务所需的产品候选事实；实际影响对象与后续变更由控制面核验",
    "agentWork.effect.legal": "提供客户通知要求的候选事实；是否需要更新由本次依赖与影响检查决定",
    "agentWork.effect.finance": "提供当前任务所需的财务候选事实；财务规则变更须经对应负责人批准后由控制面生效",
    "agentWork.effect.gtm": "提供报价或影响分析候选；最终报价只能由控制面写入",
    "agentWork.effect.reviewer": "第一轮阻止不完整候选，第二轮只放行组装；不授予人工批准或写入权",
    "topology.boundary.active": "当前业务运行 · {suggestion} · 已冻结回执 · 本地验证 · 非分布式生产",
    "topology.boundary.activeOac": "OAC 已约束本次任务与最小上下文 · {suggestion} · 本地验证 · 非分布式生产",
    "modelSuggestion.vertexLive": "真实 Vertex 模型建议",
    "modelSuggestion.local": "本地模型建议",
    "modelSuggestion.receipt": "模型建议回执",
    "topology.boundary.fallback": "当前业务运行 · {mode} · AgentTeams {agentteams} · 候选运行时 {runtime}",
    "topology.stage.oac": "01 · OAC 任务形成",
    "topology.stage.manager": "02 · 确定性协调",
    "topology.stage.domains": "03 · 领域首轮",
    "topology.stage.recovery": "04 · 审查、工具恢复与重规划",
    "topology.stage.skill": "05 · 智能体组装候选与控制验收",
    "topology.stage.human": "06 · 人工审批",
    "topology.stage.canonical": "07 · 规范状态",
    "topology.role.oacFormation": "OAC 组织任务形成",
    "topology.name.oacFormation": "已批准组织契约 → 本次最小团队",
    "topology.status.oacFormation": "已选择 {domains} 个领域 · 与实际 AgentTeams 拓扑一致",
    "topology.detail.upstream": "上游依赖",
    "topology.detail.upstreamCount": "{count} 项已验收上游任务",
    "topology.detail.noUpstream": "无上游业务任务",
    "topology.detail.input": "输入范围",
    "topology.detail.inputCount": "{count} 项受控输入已绑定",
    "topology.detail.output": "输出性质",
    "topology.detail.candidateOutput": "候选结果 · 不自动生效",
    "topology.detail.canonicalOutput": "已批准的内部规范状态",
    "topology.detail.controlOutput": "验收或审批记录 · 不直接改写业务状态",
    "topology.detail.authority": "权限边界",
    "topology.detail.candidateAuthority": "只产生候选，不能写入规范状态",
    "topology.detail.canonicalAuthority": "仅 OrgRebase 控制面可写入已批准状态",
    "topology.detail.controlAuthority": "只参与验收或授权，不直接改写业务规范状态",
    "topology.detail.execution": "执行方式",
    "topology.detail.task": "本步任务",
    "topology.detail.context": "最小上下文",
    "topology.detail.handoff": "交接到",
    "topology.detail.outputSummary": "产生结果",
    "topology.detail.reviewDecision": "审查结论",
    "topology.detail.tool": "智能体调用的 Tool",
    "topology.detail.skill": "智能体调用的 Skill",
    "topology.detail.effect": "业务影响",
    "topology.detail.oacCompiler": "确定性任务形成与最小上下文编译器",
    "topology.detail.logicalEventTime": "按本次受控业务事件时间校验",
    "topology.detail.agentExecution": "智能体生成候选结果，后续控制门负责验收",
    "topology.detail.toolExecution": "受控工具调用，返回事实供后续验收",
    "topology.detail.skillExecution": "版本化 Skill 执行，结果经发布门控",
    "topology.detail.invocation": "智能体本步调用",
    "topology.detail.invocationReceipt": "调用回执",
    "topology.detail.technicalAudit": "查看技术审计标识",
    "topology.detail.humanExecution": "负责人阅读影响摘要后明确点击批准",
    "topology.detail.storeExecution": "事务写入并封存事件链",
    "topology.detail.controlExecution": "确定性校验与控制面裁决",
    "topology.role.manager": "控制面任务协调器",
    "topology.name.managerRole": "控制面任务协调器",
    "topology.status.managerRole": "已规划 · 不属于 AgentTeams 原生智能体任务",
    "topology.detail.managerBoundary": "确定性主机协调 · 不伪装为原生管理任务",
    "topology.status.atReceived": "AgentTeams 原生接收完成 · 业务采纳由后续控制门裁决",
    "topology.status.atReceivedAbstain": "AgentTeams 已接收 · 主动放弃 · 需重新规划",
    "topology.status.atReceivedRecovered": "AgentTeams 已接收 · 缺失事实已恢复",
    "topology.status.toolRecovered": "调用依赖读取 Tool · 事实已恢复",
    "topology.status.skillComposed": "调用企业报价组装 Skill · 候选已形成",
    "topology.status.atReceivedDecision": "AgentTeams 已接收 · 复核决定 {decision}",
    "topology.role.workerA1": "执行智能体 · {domain} · 首轮",
    "topology.role.workerA2": "执行智能体 · 财务 · 第二轮",
    "topology.role.gtmCompose": "市场与商业化智能体 · 候选组装",
    "topology.role.reviewerA1": "审查智能体 · 第一轮",
    "topology.role.reviewerA2": "审查智能体 · 第二轮",
    "topology.name.quoteComposeSkill": "企业报价组装 Skill",
    "topology.role.control": "确定性控制门",
    "topology.role.human": "人工负责人 · 服务端门禁",
    "topology.status.humanApprovalBound": "第 {round} 轮 · {change}批准已绑定",
    "topology.role.canonical": "规范状态唯一写者",
    "topology.summary.oac.task": "读取已批准组织契约与员工任务，按领域需求形成本次团队",
    "topology.summary.oac.context": "组织快照、任务需求、角色权限与最小字段投影",
    "topology.summary.oac.output": "固定本次团队、任务依赖和上下文信封",
    "topology.summary.oac.effect": "不同任务可按需组队；同一运行内计划固定，变更需生成新修订",
    "topology.summary.manager.task": "将已批准计划拆成四个领域任务与审查屏障",
    "topology.summary.manager.context": "只接收 OAC 形成的执行计划和四域投影摘要",
    "topology.summary.manager.output": "AgentTeams 任务依赖图与受控绑定",
    "topology.summary.manager.handoff": "产品、法务、财务与市场和商业化领域智能体",
    "topology.summary.manager.effect": "只协调任务，不生成业务结论，也不写入规范状态",
    "topology.summary.domain.task": "{domain} 智能体只处理本领域槽位并生成候选",
    "topology.summary.domain.context": "{domain} 领域最小上下文 · {count} 项受控输入",
    "topology.summary.domain.output": "已完成的 {domain} 领域候选",
    "topology.summary.domain.abstain": "主动放弃交付：未观测到完整价格带事实",
    "topology.summary.domain.effect": "只生成领域候选；未经审查与控制门不会生效",
    "topology.summary.review1.task": "汇总四域候选并校验槽位完整性",
    "topology.summary.review1.context": "产品、法务、财务与市场和商业化首轮候选",
    "topology.summary.review1.output": "返回重新规划请求",
    "topology.summary.review1.decision": "财务候选缺少价格带字段，拒绝猜测并要求补证",
    "topology.summary.review1.effect": "候选未准入；规范状态保持不变",
    "topology.summary.finance2.task": "根据重新规划请求补齐财务价格带事实",
    "topology.summary.finance2.context": "首轮缺口与只读依赖证据",
    "topology.summary.finance2.output": "补齐后的财务领域候选",
    "topology.summary.finance2.effect": "Tool 只恢复缺失事实；本步规范写入仍为 0",
    "topology.summary.review2.task": "重新校验补证后的四域候选",
    "topology.summary.review2.context": "三个已通过领域候选与财务第二轮候选",
    "topology.summary.review2.output": "验收通过的候选结果",
    "topology.summary.review2.decision": "四域槽位、来源与作用域均通过确定性校验",
    "topology.summary.review2.effect": "允许候选进入组装阶段；仍未写入规范状态",
    "topology.summary.gtm.task": "审查通过后，由市场和商业化智能体组装四域结果",
    "topology.summary.gtm.context": "审查通过结果、四域候选与 Tool 回执",
    "topology.summary.gtm.output": "待控制门验收的企业报价候选",
    "topology.summary.gtm.effect": "Skill 由智能体调用，不是独立参与者；本步目标写入为 0",
    "topology.summary.control.task": "校验运行、摘要、Skill 发布状态和候选权限边界",
    "topology.summary.control.context": "同一运行编号的 AgentTeams、Tool 与 Skill 回执",
    "topology.summary.control.output": "原子形成企业报价协作工作稿",
    "topology.summary.control.handoff": "人工变更审批阶段",
    "topology.summary.control.effect": "形成由控制面事务提交；智能体、Tool 和 Skill 仍无规范写权",
    "topology.summary.human.task": "审阅 {change} 的影响摘要并显式点击批准",
    "topology.summary.human.context": "零写预演、影响范围、证据不足项与负责人身份",
    "topology.summary.human.output": "绑定于本轮预演的人工批准回执",
    "topology.summary.human.handoff": "OrgRebase 规范状态控制面",
    "topology.summary.human.effect": "只授权本次精确变更；不代表客户接受或对外发布",
    "topology.summary.store.task": "消费已批准变更并执行选择性重构",
    "topology.summary.store.context": "批准回执、形成回执与当前规范版本",
    "topology.summary.store.output": "已提交的 {quote}",
    "topology.summary.store.handoff": "任务验收结果与审计记录",
    "topology.summary.store.effect": "只重建受影响项，保留可证明未受影响项；证据不足则转人工",
    "acceptance.story.currentComplete": "已核验本次运行的 {count} 份变更回执与当前成果；需人工核查的事项仍保留。",
    "acceptance.story.currentPending": "当前成果与各项决定见工作台；完整回执尚未核验，不将等待或未知状态视为完成。",
    "acceptance.story.complete": "当前任务已验收：两次人工批准、两份选择性重构回执与最终报价均已绑定同一运行。",
    "acceptance.story.waiting": "当前任务尚未形成可验收终态；页面不预先计算节省数据，也不伪造结果。",
    "acceptance.story.observed": "已观测",
    "acceptance.story.pending": "等待完成",
    "acceptance.story.step.employee": "员工确认工作需求",
    "acceptance.story.step.employeeDetail": "员工只确认任务范围，不直接形成报价或业务批准。",
    "acceptance.story.step.auto": "系统自动组队与协作",
    "acceptance.story.step.autoDetail": "OAC 形成最小团队；领域候选在契约、来源、范围、权限与完整性校验通过后自动准入，证据不足才升级。",
    "acceptance.story.step.product": "产品负责人批准上线日期",
    "acceptance.story.step.productDetail": "查看零写预演后，精确负责人通过服务端审阅时间门再点击。",
    "acceptance.story.step.finance": "财务负责人批准报价币种",
    "acceptance.story.step.financeDetail": "只批准本轮币种变更；陈旧批准或负责人不匹配会被拒绝。",
    "acceptance.story.step.apply": "控制面选择性写入",
    "acceptance.story.step.applyDetail": "只应用已批准字段，保留可证明未受影响项，最终结果可下载。",
    "acceptance.story.candidateBoundary": "候选处理边界",
    "acceptance.story.candidateEmployee": "员工任务：员工确认范围",
    "acceptance.story.candidateDomain": "领域候选：校验通过自动准入，不足才升级",
    "acceptance.story.candidateCanonical": "规范变更：精确领域负责人批准",
    "acceptance.story.candidateSkill": "Skill 经验候选：独立评测后由能力负责人批准",
    "acceptance.story.change.launch": "产品上线日期",
    "acceptance.story.change.currency": "报价币种",
    "acceptance.story.change.delta": "{from} → {to}",
    "acceptance.story.change.owner": "批准人：{owner}",
    "acceptance.story.change.wait": "审阅时间门：已满足",
    "acceptance.story.change.metrics": "重建 {rebuilt} · 保留 {preserved} · 转人工 {unknown}",
    "acceptance.story.change.safety": "错误作废 {falseInvalidations} · 越权泄露 {unauthorized}",
    "acceptance.story.change.waiting": "本轮变更尚未形成完整回执。",
    "acceptance.story.final": "最终可验收结果",
    "acceptance.story.finalSummary": "{quote} · 上线 {launch} · 币种 {currency}",
    "acceptance.story.finalWaiting": "等待两次批准、选择性重构回执和最终报价同运行校验通过。",
    "value.process.deliveryBoundary": "企业目标流程 · 当前系统不管理对客发布或客户接受",
    "lifecycle.kicker": "报价 v1 基线组队回看 · 非当前变更集重跑",
    "lifecycle.title": "基线 AgentTeams 原生任务生命周期",
    "lifecycle.waiting": "等待当前运行",
    "lifecycle.run": "同一运行 · {actions} 个原生操作 · 本地验证 · 非分布式生产",
    "lifecycle.authorityBoundary": "AgentTeams 已接收 ≠ 控制面已准入 ≠ 人工已批准 ≠ 规范状态已写入",
    "lifecycle.boundary": "本轨只显示本次已验证业务运行的原生任务事实；冲突重派、进程恢复与补偿属于独立验证运行，不并入本轨。",
    "lifecycle.step.project": "在依赖图中创建任务",
    "lifecycle.step.project.detail": "{revisions} 版计划 · {tasks} 个原生任务",
    "lifecycle.step.delegate": "任务委派",
    "lifecycle.step.delegate.detail": "{count}/{total} 个完成绑定",
    "lifecycle.step.ack": "接单确认",
    "lifecycle.step.ack.detail": "{count}/{total} 个绑定在接单确认后执行",
    "lifecycle.step.handoff": "上下文 / 结果交接",
    "lifecycle.step.handoff.detail": "{context}/{total} 个上下文绑定 · {result}/{total} 个结果交接",
    "lifecycle.step.submitCheck": "提交与验收",
    "lifecycle.step.submitCheck.detail": "{count}/{total} 个原生绑定完成往返校验",
    "lifecycle.step.received": "AgentTeams 已接收",
    "lifecycle.step.received.detail": "{count}/{total} 个结果已由 AgentTeams 接收 · 不等于控制面准入",
    "lifecycle.step.terminal": "项目终态",
    "lifecycle.step.terminal.detail": "{status} · 共 {actions} 个原生操作",
    "lifecycle.state.observed": "已观测",
    "lifecycle.state.partial": "部分观测",
    "lifecycle.state.waiting": "未观测",
    "lifecycle.stage.create": "创建",
    "lifecycle.stage.delegate": "委派",
    "lifecycle.stage.ack": "接单",
    "lifecycle.stage.handoff": "上下文交接",
    "lifecycle.stage.submit": "提交",
    "lifecycle.stage.check": "校验",
    "lifecycle.stage.accept": "验收",
    "lifecycle.stage.complete": "完成",
    "lifecycle.action.createProject": "创建项目",
    "lifecycle.action.delegateTask": "委派任务",
    "lifecycle.action.ackTask": "确认接单",
    "lifecycle.action.bindContext": "绑定上下文投影摘要",
    "lifecycle.action.submitTask": "提交任务结果",
    "lifecycle.action.checkTask": "校验任务结果",
    "lifecycle.action.acceptTask": "验收任务结果",
    "lifecycle.action.completeProject": "结束项目",
    "collaboration.terminal.title": "协作工作稿 → 币种确认稿的选择性变化",
    "experience.kicker": "业务终态后的治理扩展",
    "experience.title": "经验候选 → 受治理 Skill",
    "experience.explanation": "系统只从本次真实重规划与补证轨迹提炼候选；候选不会自动改写 Skill，必须由 Skill 治理负责人审阅。",
    "experience.source": "来源",
    "experience.evaluation": "评测",
    "experience.evaluation.detail": "八分区 · 规范写入=0",
    "experience.authority": "权威边界",
    "experience.authority.detail": "未批准不可发现、装载或调用",
    "experience.release": "发布结果",
    "experience.steward": "人工责任人",
    "experience.waitingTerminal": "等待内部变更闭环终态",
    "experience.reject": "拒绝候选",
    "experience.approve": "批准进入受控灰度试调用",
    "experience.boundary": "单次运行经验种子 · 本次报价不消费该候选 · 批准后仅做受控本地发布试调用，不宣称生产泛化。",
    "experience.status.awaiting": "等待人工批准",
    "experience.status.approved": "已批准 · 可受控灰度试调用",
    "experience.status.rejected": "候选已拒绝",
    "experience.status.none": "本次运行无候选",
    "experience.review.wait": "服务端审阅门禁 · 还需 {seconds}s",
    "experience.review.ready": "审阅时间已满 · 请 Skill 治理负责人明确决策",
    "experience.review.approved": "人工批准已绑定候选、评测与前任发布头",
    "experience.review.rejected": "人工已拒绝 · 不可发现或调用",
    "experience.review.none": "没有足够证据提炼可治理候选",
    "experience.release.pending": "未发布 · 仅候选",
    "experience.release.rejected": "拒绝 · 无发布头",
    "experience.release.canary": "发布试调用通过 · 未被本次报价使用",
    "experience.toast.approved": "经验候选已由 Skill 治理负责人批准进入受控灰度试调用。",
    "experience.toast.rejected": "经验候选已拒绝，不会进入 Skill 发布链。",
    "experience.toast.wait": "请先完成经验候选审阅（{seconds}s）。",
    "experience.button.review": "审阅候选 · {seconds}s",
    "experience.pattern": "财务缺失价格带字段 → 主动放弃交付 → 重新规划 → 工具补全事实 → 验收通过",
    "capability.current.quoteCompose": "将四个领域结果组装为待验收的企业报价候选",
    "capability.current.success": "候选已形成 · 规范状态写入 0",
    "capability.current.receipt": "同运行回执已绑定",
    "capability.current.package": "企业报价组装 Skill · {version}",
    "capability.current.waiting": "等待当前任务产生 Skill 调用回执",
    "skill.source.kicker": "已发布 Skill 审阅",
    "skill.source.boundary": "已发布原文只读且不可覆盖；修改会保存为待评测的候选草稿，不影响当前智能体运行。",
    "skill.source.close": "关闭 Skill 原文审阅",
    "skill.source.version": "已发布版本",
    "skill.source.state": "当前状态",
    "skill.source.draftCount": "已保存候选",
    "skill.source.proposedVersion": "候选版本",
    "skill.source.versionHint": "新版本必须与原文头部的 version 完全一致。",
    "skill.source.publishedText": "已发布原文（只读）",
    "skill.source.draftText": "候选草稿原文（可编辑）",
    "skill.source.technical": "技术校验信息",
    "skill.source.packageDigest": "能力包摘要",
    "skill.source.textDigest": "原文摘要",
    "skill.source.savedDrafts": "已保存候选草稿",
    "skill.source.draftsBoundary": "只是草稿；尚未评测、尚未发布、不可执行",
    "skill.source.cancel": "取消修订",
    "skill.source.revise": "基于此版本创建修订",
    "skill.source.save": "保存候选草稿",
    "skill.source.catalogTitle": "能力原文与修订入口",
    "skill.source.catalogBoundary": "打开的是已发布包中的精确原文；任何修改都只生成新候选。",
    "skill.source.catalogLoading": "正在校验并读取已发布 Skill 原文…",
    "skill.source.catalogUnavailable": "Skill 原文当前无法校验；页面不伪造已发布内容。",
    "skill.display.enterpriseQuoteCompose": "企业报价组装 Skill",
    "skill.display.structuredDomainHandoff": "结构化领域交接 Skill",
    "skill.display.enterpriseLaunchReadiness": "企业上线准备度 Skill",
    "skill.display.stableId": "稳定标识：{id}",
    "skill.source.bytes": "{count} 字节已校验",
    "skill.source.draftCountValue": "{count} 个候选草稿",
    "skill.source.open": "查看原文与修订",
    "skill.source.publishedImmutable": "已发布 · 原文不可覆盖",
    "skill.source.readonlyStatus": "正在查看已发布的精确原文。如需修改，请创建独立候选草稿。",
    "skill.source.editingStatus": "正在编辑候选草稿；保存不会改写已发布版本，也不会让智能体立即使用。",
    "skill.source.savedStatus": "候选草稿 {version} 已持久化；待评测、未发布、不可执行。",
    "skill.source.errorStatus": "候选草稿未保存：{message}",
    "skill.source.noDrafts": "尚无候选草稿。",
    "skill.source.draftReceipt": "候选 {version} · 待评测 · 未发布 · 不可执行",
    "skill.source.draftOwner": "修订人：Skill 治理负责人",
    "skill.source.invalidVersion": "请填写有效的语义化版本号。",
    "formationProof.title": "动态组队计划对账",
    "formationProof.kicker": "按需组队证明 · 独立运行",
    "formationProof.loading": "读取动态组队运行…",
    "formationProof.match": "动态组队计划 {planned} 个领域 = AgentTeams 实际 {actual} 个领域 · 审查屏障 {reviewers} 个 · {actions} 个原生操作",
    "formationProof.boundary": "按需选择 {domains} · 候选态 · 0 次规范写入 · 独立固定版本进程内复验",
    "formationProof.unavailable": "动态组队证据不可用 · 保持关闭 · 未展示计划或实际拓扑",
    "formationProof.planRole": "确定性动态组队编译",
    "formationProof.planName": "最小领域计划",
    "formationProof.planStatus": "计划 {planned} = 实际 {actual}",
    "formationProof.workerRole": "领域智能体 · {domain}",
    "formationProof.reviewerRole": "独立审查屏障",
    "formationProof.reviewerName": "独立审查智能体",
    "formationProof.summary": "计划 {planned} 域 = 实际 {actual} 域 · {actions} 个原生操作",
    "formationProof.runBoundary": "{runId} · 只产生候选 · 规范写入 {writes}",
    "retained.description": "另一条冻结运行：先对账两领域动态组队计划与实际 AgentTeams 拓扑，再独立检查权威边界、冲突裁决、重派和迟到结果隔离。它不是当前报价任务的第二次执行。",
    "mechanism.strategy.orgrebase-workspace": "完整参考实现",
    "mechanism.strategy.safe-single-agent-admitted-context": "安全单 Agent（已准入上下文）",
    "mechanism.strategy.unified-raw-context-stress": "统一原始上下文压力对照",
    "mechanism.strategy.natural-language-multi-agent": "自然语言多 Agent",
    "mechanism.strategy.broadcast-invalidate-all": "广播式全部失效",
    "mechanism.strategy.no-reference-monitor": "不启用引用监控",
    "mechanism.strategy.no-context-projection": "不投影上下文",
    "mechanism.strategy.no-manifest-bijection": "不校验清单双向对应",
    "mechanism.strategy.no-certificate": "不使用证书",
    "mechanism.strategy.no-successor-promotion": "不提升后继版本",
    "mechanism.strategy.no-unknown": "不处理 Unknown",
    "mechanism.strategy.no-skill-requalification": "不重新验证 Skill 资格",
    "mechanism.strategy.non-exact-skill-digest-negative-control": "非精确 Skill 摘要负对照",
    "mechanism.title": "参考机制对照 · 合成案例",
    "mechanism.boundary": "OWB v1.1 · 平行 ReferenceWorkspaceBenchmarkSUT · 每策略 192 个合成案例。不是打包产品或真实 LLM 对比，不代表当前任务、客户效果或 ROI。",
    "mechanism.caution": "100 为受控参考基准的满值，不代表实际业务收益。总分达到 90 分仍须通过全部硬门；部分消融通过，不能据此证明每个机制在所有场景都有增益。固定 completed_at 不作为真实执行时间。",
    "mechanism.unavailable": "参考实现档案缺失或校验失败，已清空结果；不影响当前报价。",
    "mechanism.ready": "已校验 13 个策略 · 同一 192 案例集合 · 只读历史结果",
    "mechanism.profile": "策略",
    "mechanism.score": "基准分 / 100",
    "mechanism.gates": "硬门",
    "mechanism.result": "综合判定",
    "mechanism.reference": "参考实现",
    "mechanism.baselines": "基线",
    "mechanism.ablations": "消融",
    "mechanism.source": "源文件 SHA-256",
    "mechanism.path": "源码包内路径",
    "mechanism.quoteDigest": "绑定报价价值回执摘要",
    "mechanism.registryDigest": "基准规则摘要",
    "mechanism.threshold": "通过阈值：90 + 全部硬门",
    "retained.archive.title": "组队、权限与结果隔离",
    "retained.archive.summary": "本地运行记录 · 独立于当前任务",
    "retained.quoteLoop.kicker": "控制语义审计",
    "retained.quoteLoop.title": "权威边界、冲突与结果隔离",
    "retained.quoteLoop.boundary": "另一条受控验证运行 · 只证明控制语义 · 不重复当前业务流程",
    "retained.authority.detail": "AgentTeams 只产生调度与候选事实",
    "retained.boundary.detail": "独立保留的后继运行不与当前试点合并",
    "retained.successor.detail": "脚本化本地批准 · 仅 OrgRebase 规范状态控制面可写",
    "retained.unavailable": "机制验证记录不可用 · 不推断结果",
    "retained.empty": "机制验证记录不可用；未推断、未补造。",
    "operations.events.waiting": "等待事件",
    "operations.events.scoped": "报价业务链 {quoteStatus} · {quote} 个事件；工作区全局链 {global} 个，其中 OAC 治理 {oac} 个",
    "operations.persistence.detail": "持久化与恢复状态",
    "operations.sameRun.kicker": "当前业务运行证据",
    "operations.sameRun.verified": "当前运行的报价形成子链已形成 5/5 段可校验回执",
    "operations.sameRun.verifiedComplete": "当前业务运行已完成：报价形成 5/5 段回执、两次人工审批和选择性重构均已校验",
    "operations.sameRun.partial": "当前运行的报价形成子链已形成 {observed}/{total} 段回执",
    "operations.sameRun.unavailable": "当前任务尚未形成业务运行回执",
    "operations.sameRun.projectionVerified": "当前运行终态投影已校验 · 七层因果结构完整",
    "operations.sameRun.projectionWaitingArchive": "当前任务已终态 · 等待当前运行归档通过后再请求观测投影",
    "operations.sameRun.projectionLoading": "当前运行归档已通过 · 正在请求终态观测投影",
    "operations.sameRun.projectionClosed": "终态观测投影已关闭 · 保留五段报价形成证据",
    "operations.sameRun.boundary": "本区只证明当前运行的报价形成子链：每段必须同时匹配当前运行编号和回执摘要。人工审批与业务终态必须由本页任务验收卡另行证明；OTLP、告警、容量和恢复位于运行保障页。",
    "operations.sameRun.boundaryComplete": "本区的 5 段回执证明当前运行的报价形成子链；上方任务验收卡已校验同一运行的两次人工审批、选择性重构和终态回执。OTLP、告警、容量与恢复属于运行保障页的独立工程验证，不借给当前运行。",
    "operations.sameRun.projectionBoundary": "浏览器只对当前运行的七层因果结构、关联字段与零写入声明做交叉校验；OTLP 摘要由服务端封口，本页不在浏览器重算。它仍是受控本地终态投影，不是实时观测、生产后端或 SLA 证明。",
    "operations.sameRun.projectionClosedBoundary": "当前归档或投影未通过同运行交叉校验，终态观测投影保持关闭；五段报价形成回执仍可查看，冻结档案不会被借用，也不影响已单独校验的业务归档。",
    "operations.sameRun.currentLane": "当前运行 · 报价形成子链 · 5 段",
    "operations.sameRun.projectionLane": "当前运行 · 终态因果投影 · 7 层",
    "operations.sameRun.projectedNodeDetail": "结构交叉校验 · 受控本地 · 外部目标写入 0 · 服务端封口摘要已绑定",
    "operations.sameRun.candidateLane": "候选因果追踪 · 5 段",
    "operations.sameRun.observabilityLane": "OTLP 观测载体",
    "operations.sameRun.governanceLane": "治理后继 · 同一运行链路独立包",
    "operations.retained.title": "历史运维验证",
    "operations.retained.boundary": "独立运行记录",
    "operations.delivery.title": "独立工程交付验证",
    "operations.otlp.detail": "追踪 / 日志 / 指标可查询",
    "operations.readiness.detail": "本机制品已验证；生产 SLA / HA / 异地灾备 / 供应链签名需企业环境验收",
    "operations.audit.title": "运行保障详情",
    "operations.audit.summary": "展开查看可观测、连接、留存与生产验收边界",
    "operations.audit.unavailable": "经校验的运维摘要不可用；本页不推断、不补造状态。",
    "operations.audit.run.title": "独立验证运行内关联",
    "operations.audit.runId": "精确运行编号",
    "operations.audit.chain": "候选因果链",
    "operations.audit.chainPass": "五段因果追踪已关联，治理写入为独立后继包",
    "operations.audit.otlp.title": "OTLP 观测合同",
    "operations.audit.signals": "已导出且可查询的信号",
    "operations.audit.fields": "关联字段（{count} 个）",
    "operations.audit.signal.traces": "追踪",
    "operations.audit.signal.logs": "日志",
    "operations.audit.signal.metrics": "指标",
    "operations.audit.field.deploymentEnvironment": "部署环境",
    "operations.audit.field.serviceName": "服务名称",
    "operations.audit.field.serviceVersion": "服务版本",
    "operations.audit.field.runId": "运行编号",
    "operations.audit.field.taskId": "AgentTeams 任务编号",
    "operations.audit.field.skillName": "Skill 名称",
    "operations.audit.field.toolReceipt": "工具回执摘要",
    "operations.audit.field.receipt": "运行回执摘要",
    "operations.audit.field.targetWrites": "目标写入数",
    "operations.audit.connectors.title": "连接器状态",
    "operations.audit.connector.source": "本地数据源 HTTP",
    "operations.audit.connector.tool": "本地工具 HTTP",
    "operations.audit.connector.crm": "客户关系管理（CRM）",
    "operations.audit.connector.cpq": "配置、定价与报价（CPQ）",
    "operations.audit.connector.clm": "合同全生命周期管理（CLM）",
    "operations.audit.connector.erp": "企业资源计划（ERP）",
    "operations.audit.connector.knowledgeBase": "企业知识库",
    "operations.audit.connector.pendingEnterprise": "待企业环境接入",
    "operations.audit.retention.title": "留存与清理边界",
    "operations.audit.indexedTelemetry": "可索引遥测留存",
    "operations.audit.rejectedDiagnostics": "被拒载荷诊断留存",
    "operations.audit.businessEvidence": "规范业务证据",
    "operations.audit.businessEvidenceUnaffected": "不由遥测留存清理删除",
    "operations.audit.rejectedRaw": "被拒原始载荷",
    "operations.audit.rawNotPersisted": "不持久化",
    "operations.audit.retentionPeriod": "{days} 天（{seconds} 秒）",
    "operations.audit.readiness.title": "部署与生产边界",
    "operations.audit.deployment": "部署配置",
    "operations.audit.productionReady": "生产就绪",
    "operations.audit.productionPilot": "否 · 当前为受控试点 MVP",
    "operations.audit.contractualSla": "合同 SLA",
    "operations.audit.geoDr": "异地灾备切换",
    "operations.audit.sbom": "软件物料清单（SBOM）",
    "operations.audit.contributing": "贡献指南",
    "operations.audit.localBoundary": "本页只展示受控本地验证摘要；不展开原始载荷，不把尚待企业环境验收的生产能力标为已完成。",
    "operations.evidenceIndex.title": "证据索引 · 冻结独立验证档案",
    "operations.evidenceIndex.summary": "展开查看经校验的索引、证据等级与隐私拒绝结果",
    "operations.evidenceIndex.facts": "冻结索引最小投影",
    "operations.evidenceIndex.runId": "档案运行编号",
    "operations.evidenceIndex.entryCount": "索引条目",
    "operations.evidenceIndex.indexDigest": "索引摘要",
    "operations.evidenceIndex.packDigest": "证据包摘要",
    "operations.evidenceIndex.classes": "证据等级",
    "operations.evidenceIndex.verifier": "索引验证器",
    "operations.evidenceIndex.privacy": "隐私探针 / 拒绝",
    "operations.evidenceIndex.targetWrites": "只读索引目标写入",
    "operations.evidenceIndex.boundary": "另一条冻结运行 · 非本次报价运行 · 不借证；只展示摘要、证据等级和拒绝原因，不展开原始或敏感值。",
    "operations.evidenceIndex.verified": "索引闭包、文件摘要与隐私拒绝已校验",
    "operations.evidenceIndex.unavailable": "冻结证据索引不可用；本页不展示部分结果。",
    "operations.evidenceIndex.entryCountValue": "{count} 条 · 全量文件绑定",
    "operations.evidenceIndex.privacyValue": "{status} · {canary} / {rejection} · 原始载荷未保留",
    "operations.evidenceIndex.targetWritesValue": "{count} 次（只读投影）",
    "operations.failures.title": "异常处理规则",
    "operations.failures.boundary": "静态处理规则 · 运行收据见本页独立验证档案",
    "operations.failure.403.title": "负责人不匹配",
    "operations.failure.403.detail": "拒绝批准 · 规范写入=0",
    "operations.failure.409.title": "陈旧 / 冲突",
    "operations.failure.409.detail": "拒绝应用 · 保留当前规范状态",
    "operations.failure.block.title": "报价包 / 数据库不匹配",
    "operations.failure.block.detail": "启动前阻断 · 不自动迁移",
    "operations.failure.kill.title": "独立进程恢复",
    "operations.health.unavailable": "/api/health 未返回",
    "operations.ready.unavailable": "无法读取 /readyz 的有效状态",
    "operations.ready.confirmed": "服务就绪探针已通过",
    "operations.ready.deployment": "当前部署：{maturity}",
    "operations.ready.production.false": "未声明生产就绪",
    "operations.ready.production.true": "部署报告声明生产就绪；探针不替代生产验收",
    "operations.ready.profile": "画像 {digest}",
    "operations.ready.pack": "规则包 {digest}",
    "operations.ready.maturity.REFERENCE_RUNTIME": "参考运行时",
    "operations.ready.maturity.SINGLE_ENTERPRISE_PILOT": "单企业试点",
    "operations.ready.maturity.AUTHENTICATED_SINGLE_TENANT": "已认证单租户",
    "operations.ready.notReady": "未就绪",
    "operations.ready.notReadyDetail": "就绪探针报告服务尚未就绪",
    "operations.workspaceStoreConnected": "工作区存储已连接",
    "operations.persistence.checking": "正在核对当前工作区存储连接。",
    "operations.persistence.ready": "当前工作区可读，存储就绪探针通过。",
    "operations.persistence.unavailable": "当前工作区存储连接尚未确认；保留记录不能证明此刻可用。",
    "operations.boundary.active": "当前业务运行已完成本地闭环并验证 SQLite 完成态重开，但未演示该业务运行的中断续跑。独立本地运维运行已验证 2 次 SIGKILL 后的重试意图与已提交结果采纳；自主分布式 AgentTeams 的跨进程恢复属于生产验证范围。真实企业连接器与数据、真实 ROI、生产 SLA / HA / 异地灾备 / 多租户仍需企业环境验收。",
    "operations.boundary.formation": "当前业务运行只已验证报价形成子链；两次人工审批、选择性重构与终态回执尚未全部完成，不得视为完整业务闭环。下方 OTLP、告警、容量与恢复属于另一条独立工程验证档案。",
    "operations.boundary.inactive": "当前覆盖受控本地试点与运维合同；真实连接器、企业 ROI、生产 SLA / HA / 异地灾备 / 多租户仍需企业环境验证。",
    "quote.empty.title": "尚未形成内部报价工作稿",
    "quote.empty.body": "OAC 企业接入完成后，才能生成报价 v1 协作工作稿。启动后先执行固定版本的基线组队；后续变更集才从持久收据投影最小变更团队并复用已准入能力。",
    "quote.field.customer": "客户 / 报价负责人",
    "quote.field.plan": "企业方案",
    "quote.field.launch": "上线日期",
    "quote.field.residency": "数据驻留",
    "quote.field.notice": "客户通知",
    "quote.field.price": "价格带",
    "quote.field.currency": "币种",
    "quote.field.terms": "合作条款",
    "quote.field.workflow": "内部工作状态",
    "quote.field.decision": "当前变更决策",
    "quote.field.externalLifecycle": "外部商业生命周期",
    "quote.field.lineage": "修订轨迹",
    "quote.revision": "受控工作稿 · 技术更新 {number}",
    "quote.stage.collaborative": "协作工作稿",
    "quote.stage.launchConfirmed": "上线日期确认稿",
    "quote.stage.currencyConfirmed": "币种确认稿",
    "quote.workflow.revision1": "协作工作稿 · 等待产品日期变更",
    "quote.workflow.revision2": "上线日期确认稿 · 等待财务币种变更",
    "quote.workflow.revision3": "币种确认稿 · 内部变更闭环已校验",
    "quote.lineage.summary": "{path} · 受治理选择性重构",
    "quote.lineage.current": "当前 {version}",
    "quote.external.unmanaged": "本系统未管理 · 未观测对客发布或客户接受证据",
    "quote.decision.none": "无待处理人工变更决策",
    "quote.decision.launchPending": "产品日期变更 · 等待产品负责人批准",
    "quote.decision.launchApproved": "产品日期变更 · 已批准，等待控制面应用",
    "quote.decision.currencyPending": "币种变更 · 等待财务负责人批准",
    "quote.decision.currencyApproved": "币种变更 · 已批准，等待控制面应用",
    "quote.coalition.title": "最小领域联盟",
    "quote.coalition.ready": "4 个领域 · 已覆盖",
    "quote.defaultLabel": "企业报价条件包",
    "quote.customerUnknown": "未声明客户",
    "quote.ownerUnknown": "未声明负责人",
    "quote.notice.required": "需要通知",
    "quote.notice.notRequired": "无需通知",
    "domain.product": "产品",
    "domain.product.detail": "方案 · 日期 · 驻留",
    "domain.legal": "法务",
    "domain.legal.detail": "通知义务",
    "domain.finance": "财务",
    "domain.finance.detail": "价格带 · 币种",
    "domain.gtm": "市场与商业化",
    "domain.gtm.detail": "报价组装 · 合作条款",
    "impact.title": "审批前预演 + 最小重构证书（VMRC）",
    "impact.vmrcBoundary": "VMRC 将变更集、预演与逐对象效果精确绑定：漏重建、多重建，或把未知项当作可保留，都会拒绝应用。",
    "impact.rebuild": "必须重建",
    "impact.preserve": "证明可留",
    "impact.unknown": "证据不足",
    "impact.requalify": "Skill 重验",
    "impact.empty": "变更预演后，这里会显示每个目标的分类、证明路径与证书。",
    "impact.emptyLocked": "预演已锁定，但响应中没有目标明细。",
    "impact.noProofPath": "未声明证明路径",
    "governance.title": "批准、继任图与事件链",
    "governance.approver": "批准人",
    "governance.noEvents": "未产生业务事件",
    "governance.events": "{count} 个报价业务事件 · 完整性通过",
    "governance.passSeal": "核",
    "governance.waitSeal": "待",
    "governance.zeroWrites": "0 次目标写入",
    "footer.copy": "OrgRebase · 企业工作持续演化引擎 · 受控演示场景",
    "footer.api": "开发者接口（OpenAPI）",
    "common.loading": "读取中…",
    "common.currentValue": "当前值",
    "common.declaredVersion": "已声明新版本",
    "common.evidenceUnavailable": "证据不可用 · 不推断结果",
    "action.empty.label": "生成内部工作稿",
    "action.empty.title": "形成可演化的企业报价基线",
    "action.empty.detail": "先生成报价。规则变化后，系统将检查影响、组织候选更新，并提交对应负责人审批。",
    "action.delta.launch": "产品上线日期 · {base} → {proposed}",
    "action.delta.currency": "报价币种 · {base} → {proposed}",
    "action.empty.role": "报价运营负责人",
    "action.oacGate.label": "打开 OAC 企业适配",
    "action.oacGate.title": "组织契约未准入，报价暂未放行",
    "action.oacGate.detail": "先完成五类企业输入映射、OAC 确定性校验和 4 秒负责人准入；放行后本按钮会恢复为“生成内部工作稿”。",
    "action.oacGate.actor": "OAC 业务启动门",
    "action.oacGate.role": "确定性前置门控 · 不代替人工准入",
    "action.oacChecking.label": "正在校验 OAC 业务启动门…",
    "action.oacChecking.title": "正在读取组织契约准入状态",
    "action.oacChecking.detail": "校验完成前保持关闭；页面不提前判定已准入或未准入。",
    "action.oacChecking.actor": "OAC 业务启动门",
    "action.oacChecking.role": "正在读取服务端权威状态",
    "action.experienceReview.label": "审阅经验候选",
    "action.experienceReview.title": "经验候选等待人工治理，业务完成状态见当前回执",
    "action.experienceReview.detail": "只导航到本次运行生成的经验候选；候选不会自动发布或改写 Skill。",
    "action.experienceReview.actor": "Skill 治理负责人",
    "action.experienceReview.role": "候选发布决策者 · 显式人工权威",
    "action.launchPreview.label": "提交产品日期变化并预演",
    "action.launchPreview.title": "上游产品变化已到达，自动计算影响范围",
    "action.launchPreview.detail": "受控实例由按钮提交上游事实变化；零写入计算必须重建、证明可留、证据不足项与 Skill 重验边界，此步不要求负责人批准。",
    "action.launchPreview.role": "上游变化事件 · 自动影响计算",
    "action.launchApprove.label": "产品负责人批准本次变更",
    "action.launchApprove.title": "显式批准被锁定的产品变更预演",
    "action.launchApprove.detail": "批准绑定当前预演摘要；摘要漂移时后端会拒绝。",
    "action.launchApprove.role": "产品负责人 · 本地自声明身份 · 精确绑定",
    "action.launchApply.label": "应用变更 · 形成上线日期确认稿",
    "action.launchApply.title": "消费批准回执并执行选择性重构",
    "action.launchApply.detail": "控制面只能消费已批准摘要，不会自批准或重做预演。",
    "action.control.role": "确定性控制面 · 规范写者",
    "action.owner.role": "指定负责人 · 精确提案批准",
    "action.currencyPreview.label": "提交财务币种变化并预演",
    "action.currencyPreview.title": "上游财务政策变化已到达，自动计算影响范围",
    "action.currencyPreview.detail": "上线日期确认稿已形成持久化检查点；受控实例由按钮提交上游币种变化，系统继续零写预演，此步不要求负责人批准。",
    "action.currencyPreview.role": "上游变化事件 · 自动影响计算",
    "action.currencyApprove.label": "财务负责人批准本次变更",
    "action.currencyApprove.title": "显式批准被锁定的币种变更预演",
    "action.currencyApprove.detail": "只有财务负责人可为此变更生成与预演摘要绑定的批准。",
    "action.currencyApprove.role": "财务负责人 · 本地自声明身份 · 精确绑定",
    "action.currencyApply.label": "应用变更 · 形成币种确认稿",
    "action.currencyApply.title": "完成第二次选择性重构",
    "action.currencyApply.detail": "产生继任工作、继任依赖图、回执与可验证事件链。",
    "action.complete.label": "内部选择性重构闭环已校验",
    "action.complete.title": "币种确认稿 · 变更闭环已校验",
    "action.complete.summary": "{revision} · {launch} · {currency}",
    "action.complete.detail": "从报价包装配、两次本地负责人命令到继任图与事件链，全部可下载复核。",
    "action.complete.role": "当前报价只读视图 · 变更与复核记录见对应任务",
    "action.dynamic.start": "发起{label}",
    "currency.usd": "美元（USD）",
    "currency.eur": "欧元（EUR）",
    "action.complete.goldenRole": "只读运行视图 · 审查智能体验收通过 · 规范状态已持久化",
    "action.complete.goldenDetail": "同一业务运行中，AgentTeams、Tool、Skill、人工批准、选择性重构与事件链已闭环，可下载复核。",
    "active.boundary.scopeTitle": "查看适用范围与发布边界",
    "active.boundary.productionScope": "分布式生产 AgentTeams 与真实企业 ROI 仍需企业环境验收；智能体候选和内部批准不能替代客户发布与接受证明。",
    "active.boundary.golden": "变更已生效，报价与批准记录已关联。",
    "active.boundary.goldenFormation": "报价已生成，正在核对本任务所需的批准与生效记录。",
    "active.boundary.pending": "当前交互由 {profile} 驱动，本次业务运行尚未形成。只有 AgentTeams 候选结果、审查智能体、Tool 和 Skill 经完整性校验后，控制面才能形成报价；后续高风险变更仍需匹配负责人显式批准。",
    "active.boundary.active": "当前交互由 {profile} 驱动，报价已形成并进入 {stage}。领域智能体、审查智能体、Tool 和 Skill 只产生候选或证据；后续高风险变更仍需匹配负责人显式批准。",
    "active.boundary.oac": "当前报价已在同一形成事务中消费已准入的 OAC 激活绑定，并进入 {stage}；形成结果与只读工具回执已持久化。OAC 准入不等于业务批准，后续高风险变更仍需指定负责人显式点击。",
    "active.boundary.complete": "本次成果与对应完成回执已核验，变更、批准和重构记录按实际运行展示。本系统未管理对客发布或客户接受状态；本地记录不构成分布式生产部署或真实企业 ROI 验证。",
    "active.boundary.initial": "本次业务运行尚未形成。只有 AgentTeams 候选结果、审查智能体、Tool 和 Skill 经完整性校验后，控制面才能形成报价；后续高风险变更仍需匹配负责人显式批准。",
    "active.boundary.unavailable": "未读取到工作区权威状态。页面保持关闭，不把请求失败解释为新任务或空工作区。",
    "active.profileAdmitted": "企业报价包已入场",
    "fallback.packClosure": "验证报价包、配置档案、数据源与权威闭包",
    "fallback.formation": "形成四域联盟、上下文与协作工作稿",
    "fallback.toolRead": "{tool} · 读取完整依赖证据",
    "fallback.previewLock": "{kind} · 锁定影响预演与最小重构证书（VMRC）",
    "fallback.approval": "{kind} · 审阅锁定预演后主动批准",
    "fallback.rebase": "{kind} · 选择性重构 → 继任内部修订",
    "error.request": "请求失败（HTTP {status}）",
    "error.apiCode": "请求未完成（错误码：{code}）",
    "error.approvalWindow": "批准时间窗口无效。请核对服务端时间，重新预演并批准。",
    "error.approvalTimestamp": "批准时间格式无效。请核对时间格式，重新预演并批准。",
    "error.incident": "错误编号：{id}",
    "error.previewDigest": "缺少已锁定的预演摘要，已停手。",
    "error.approvalDigest": "缺少有效批准摘要，已停手。",
    "toast.reviewFirst": "请先完成预演审阅（{seconds}s）。",
    "toast.recordingApproval": "正在记录人工批准…",
    "toast.generatingReceipt": "正在生成可验证回执…",
    "toast.applyingApproved": "人工批准已记录，确定性控制面正在自动应用…",
    "toast.approvedAndApplied": "人工批准已绑定，选择性重构已自动完成",
    "toast.completed": "{action}：完成",
    "toast.serverGate": "服务端审阅门禁仍在等待：{seconds}s · {notBefore}",
    "toast.downloaded": "{name} 已下载",
    "toast.oacGateOpened": "已打开 OAC 企业适配；请按六个阶段完成准入。",
    "header.controlledLocal": "本地验证环境",
    "header.declaredData": "数据类型：读取中",
    "header.syntheticData": "组织背景：受控企业样例",
    "header.syntheticPricedData": "受控企业样例 · 计价输入来源见报价",
    "header.dataUnavailable": "数据类型：状态不可用",
    "header.mechanismChecking": "运行校验：检查中",
    "header.mechanismPass": "运行校验：通过",
    "header.mechanismStatus": "运行校验：{status}",
    "header.currentTask.waiting": "当前任务：待发起",
    "header.currentTask.progress": "当前任务：{stage}",
    "header.currentTask.complete": "当前任务：已完成",
    "header.currentTask.unavailable": "当前任务：状态不可用",
    "header.goldenPilot": "协作引擎：AgentTeams",
    "header.realEnterpriseNotRun": "生产就绪：否 · 待企业验证",
    "hero.scenario": "企业工作区 / 企业报价",
    "complexity.solution.kicker": "03 · OrgRebase 解法",
    "journey.kicker": "持久化工作流",
    "timeline.productOwner": "产品负责人",
    "timeline.financeOwner": "财务负责人",
    "cockpit.kicker": "单一场景 · 运行实况",
    "scene.activeRun": "当前运行",
    "scene.goldenRun": "业务端到端运行",
    "scene.stage": "当前阶段",
    "scene.packProfile": "报价包 / 配置档案",
    "scene.authority": "规范状态权威",
    "value.kicker": "变化响应 · 单一主用户",
    "value.primaryRole": "报价运营负责人",
    "value.process.initial": "八步责任链已配置",
    "value.cost.evidence": "模型化反事实",
    "value.cost.boundary": "归一化操作成本 · 不是企业 ROI",
    "collaboration.currentRun": "当前业务运行",
    "collaboration.boundary.initial": "当前 · 等待启动变化驱动案例",
    "collaboration.terminal.kicker": "终态交付物",
    "retained.kicker": "历史可靠性验证",
    "retained.boundary.initial": "已验证 · 本地环境 · 固定版本进程内 · 非实时分布式",
    "retained.authority": "权威边界",
    "retained.runBoundary": "运行边界",
    "retained.exceptions": "控制异常",
    "retained.exceptions.detail": "冲突 · 重派 · 迟到结果隔离",
    "retained.successor": "受治理后继状态",
    "retained.ledger.title": "AgentTeams 原生操作台账",
    "retained.ledger.loading": "读取并校验运行记录…",
    "table.tool": "工具",
    "operations.apiHealth": "API 健康状态",
    "operations.workspaceReady": "工作区就绪状态",
    "operations.eventChain": "工作区全局审计链",
    "operations.persistence": "持久化",
    "operations.sqliteLocal": "SQLite · 本地",
    "operations.delivery.scope": "HTTP · OTLP · 部署 · 供应链",
    "operations.sourceTool": "数据源 + 工具",
    "operations.sourceTool.detail": "真实 HTTP 协议 ≠ 企业连接器",
    "operations.otlpFields": "OTLP 关键字段",
    "operations.deploySupply": "部署 / SBOM / 指南",
    "operations.failure.kill.detail": "独立受控本地运行 · 重试意图 / 采纳已提交结果",
    "quote.kicker": "当前交付物",
    "impact.kicker": "影响判定 · 审批前",
    "impact.previewDigest": "预演证据",
    "impact.minimalCertificate": "VMRC 证书绑定",
    "impact.previewEvidenceBound": "已绑定本轮预演",
    "impact.certificateVerified": "VMRC 精确绑定通过",
    "impact.certificateInvalid": "VMRC 绑定无效 · 拒绝应用",
    "governance.kicker": "治理与恢复",
    "governance.approvalDigest": "批准摘要",
    "governance.successorGraph": "后继图",
    "governance.recovery": "恢复状态",
    "governance.dependencyTool": "依赖工具",
    "governance.toolReceipt": "工具回执",
    "governance.approvalsBound": "{count} 份人工批准已绑定",
    "governance.successorReady": "后继状态已建立",
    "governance.toolReceiptBound": "只读工具回执已绑定",
    "governance.eventChain": "事件链",
    "enum.empty": "尚未开始",
    "enum.notFormed": "尚未形成",
    "enum.notRun": "等待执行",
    "enum.stale": "与当前运行不同步",
    "enum.notObserved": "未观测",
    "enum.unavailable": "不可用",
    "enum.waiting": "等待中",
    "enum.checking": "检查中",
    "enum.pass": "通过",
    "enum.fail": "失败",
    "enum.critical": "严重",
    "enum.validated": "已验证",
    "enum.partial": "部分完成",
    "enum.completed": "已完成",
    "enum.trustedComplete": "可信完成",
    "enum.advisoryAccepted": "建议候选已接纳",
    "enum.reviewRequired": "需要人工复核",
    "enum.abstain": "主动放弃交付",
    "enum.replan": "重新规划",
    "enum.succeeded": "执行成功",
    "enum.success": "成功",
    "enum.executed": "已执行",
    "enum.fenced": "已隔离",
    "enum.differentRun": "与当前业务运行分离",
    "enum.sameRetainedRun": "同一保留后继运行",
    "enum.installedWheel": "Python Wheel 包已安装",
    "enum.rollbackDecisionRecorded": "回滚决策已记录",
    "enum.executedAndInvoked": "已执行并调用",
    "enum.signalsExported": "追踪、日志与指标已导出并查询",
    "enum.approved": "已批准",
    "enum.rejected": "已拒绝",
    "enum.locked": "已锁定",
    "enum.governed": "受治理",
    "enum.committed": "已原子提交",
    "enum.verified": "已校验",
    "enum.validatedAtomicRollback": "受控本地原子回滚已验证",
    "enum.validatedDeterministicSeparateRun": "本地确定性补偿已验证（独立运行）",
    "enum.validatedRealToolSeparateRun": "本地真实 Git 补偿已验证（独立运行）",
    "enum.candidateDelivered": "候选已交付",
    "enum.canary": "受控灰度试调用",
    "enum.current": "当前",
    "enum.healthy": "健康",
    "enum.ready": "已就绪",
    "enum.true": "是",
    "enum.false": "否",
    "enum.none": "无",
    "enum.noCandidate": "无可治理候选",
    "enum.notApplicable": "不适用",
    "enum.improve": "建议改进",
    "enum.candidateOnly": "仅候选",
    "enum.readOnly": "只读",
    "enum.canonicalWrite": "规范写入",
    "enum.approval": "人工批准",
    "enum.controlledLocal": "受控本地",
    "enum.syntheticFixture": "受控演示数据",
    "enum.singleRunSeed": "单次运行经验种子",
    "enum.failClosed": "默认拒绝",
    "enum.startupBlocked": "启动阻断",
    "enum.localDeterministic": "本地确定性",
    "type.changeSetRevision": "变更集修订",
    "type.impactPreview": "影响预演",
    "enum.persistedReopenable": "已持久化 · 完成态可重开",
    "enum.validatedProxy": "代理指标已验证",
    "enum.modelled": "模型化",
    "enum.referenceTemplate": "参考模板",
    "enum.designTargetBaseline": "参考人工基线 · 待企业校准",
    "enum.draft": "草稿",
    "enum.evaluated": "已评测",
    "enum.shadow": "受控候选验证",
    "enum.release": "已发布",
    "enum.notTriggered": "未触发",
    "enum.decisionOnly": "仅决策",
    "enum.requalificationRequired": "需重新评测",
    "enum.packageResources": "包内资源",
    "enum.present": "已提供",
    "enum.launchPreviewed": "产品日期变更待审批",
    "enum.quoteV1": "内部工作稿已形成",
    "enum.quoteV2": "产品日期变更已应用",
    "enum.quoteV3": "内部变更闭环已校验",
    "enum.launchApproved": "产品日期变更已批准 · 待应用",
    "enum.currencyPreviewed": "币种变更待审批",
    "enum.currencyApproved": "币种变更已批准 · 待应用",
    "enum.retryIntent": "重试意图",
    "enum.adoptCommitted": "采纳已提交结果",
    "enum.controlled": "受控",
    "enum.canonicalApplied": "规范写入已应用",
    "enum.nativeExecuted": "AgentTeams 原生控制面已执行",
    "enum.deterministicProviders": "确定性领域提供器",
    "enum.independentExecutors": "固定 TeamHarness + 独立执行进程",
    "enum.targetWrites": "{count} 次目标写入",
    "enum.awaitingApproval": "等待人工批准",
    "enum.approvedCanary": "已批准进入受控灰度试调用",
    "enum.waitingQuoteV3": "等待内部变更闭环终态",
    "actionLedger.applyQuote": "应用报价候选",
    "authority.canonical": "OrgRebase 规范状态控制面",
    "enum.actions": "{count} 个操作",
    "enum.processes": "{count} 个进程",
    "enum.steps": "{count} 个步骤",
    "enum.events": "{count} 个事件",
    "enum.writes": "{count} 次写入",
    "enum.attempt": "第 {count} 次尝试",
    "type.auto": "自动",
    "type.candidate": "候选",
    "type.human": "人工",
    "type.write": "写入",
    "type.tool": "工具",
    "actionLedger.admitClosure": "校验报价包、配置、数据源与权威闭环",
    "actionLedger.nativeCandidate": "执行 AgentTeams 原生候选任务流",
    "actionLedger.reviewProvenance": "重新规划后验收精确来源",
    "actionLedger.recoverFinance": "恢复财务依赖事实",
    "actionLedger.composeCandidate": "组装已审阅报价候选",
    "actionLedger.formQuote": "形成领域联盟、上下文与协作工作稿",
    "actionLedger.readDependency": "读取依赖证据",
    "actionLedger.bindAdvisory": "将已入场变更绑定到候选任务",
    "actionLedger.explainPremise": "说明权威领域前提变化，不直接认定影响",
    "actionLedger.explainImpact": "说明报价影响候选，不改写规范状态",
    "actionLedger.taskGraph": "任务图候选",
    "actionLedger.semanticExplanation": "语义解释候选",
    "actionLedger.impactCandidate": "影响分析候选",
    "actionLedger.lockLaunch": "锁定上线日期变更预演",
    "actionLedger.approveLaunch": "人工批准上线日期预演",
    "actionLedger.applyLaunch": "应用上线日期选择性重构",
    "actionLedger.lockCurrency": "锁定币种变更预演",
    "actionLedger.approveCurrency": "人工批准币种预演",
    "actionLedger.applyCurrency": "应用币种选择性重构",
    "status.dagCompleted": "任务图已规划 · 已完成",
    "status.priceMissing": "主动放弃 · 缺少价格带",
    "status.candidateAccepted": "候选已验收",
    "status.candidateAcceptedCode": "候选已验收",
    "status.governedAppliedCode": "受治理状态已写入",
    "status.controlledLocalCommandCode": "受控本地脚本化负责人命令",
    "status.projectCompleted": "项目已完成",
    "status.recoveredCandidate": "已补证 · 候选已验收",
    "status.releaseCanary": "发布 · 灰度 · 8/8 分区通过",
    "status.atomicCommit": "已校验 · 原子提交",
    "status.waitingCommit": "已校验 · 等待提交",
    "status.explicitApproval": "人工已明确批准",
    "status.waitingClick": "等待人工点击",
    "status.nextRiskWaits": "下一个高风险变更将等待人工",
    "status.contextOnly": "仅有上下文 · 未观测执行智能体运行",
    "status.previewLocked": "预演已锁定",
    "status.waitingCandidates": "等待领域候选",
    "status.explicitLocalApproval": "本地人工已明确批准",
    "status.waitingHumanClick": "等待人工点击",
    "status.noCoordinator": "未观测协调者运行",
    "status.noBinding": "未观测任务绑定",
    "experience.authority.open": "可发现 · 可装载 · 可调用",
    "experience.authority.locked": "仅候选 · 已锁定",
    "maturity.signedSynthetic": "已签名受控演示基线",
    "maturity.inProgress": "进行中",
    "maturity.nativeControl": "受控本地原生控制",
    "maturity.independentProcess": "受控本地独立进程",
    "maturity.realHttp": "受控本地真实 HTTP",
    "maturity.releaseAuthority": "同一运行受控本地发布授权",
    "maturity.highFidelitySynthetic": "高保真受控演示数据",
    "maturity.serverApproval": "服务端持久化人工批准",
    "maturity.browserIncomplete": "浏览器或当前运行未完成",
    "maturity.frozenPack": "已封存本地审计包",
    "maturity.freezePending": "当前运行审计包待封存",
    "maturity.videoPending": "等待视频批准",
    "maturity.controlledTested": "受控本地已测试",
    "maturity.completedReopen": "受控本地完成态重开",
    "maturity.runScoped": "运行级审计记录",
    "maturity.controlledObserved": "受控本地已观测",
    "maturity.retainedMvp": "已保留的 MVP 运维验证记录",
    "role.quoteOperator": "报价运营负责人",
    "role.productAgent": "产品智能体",
    "role.legalAgent": "法务智能体",
    "role.financeAgent": "财务智能体",
    "role.gtmAgent": "市场与商业化智能体",
    "role.gtmOwner": "市场与商业化负责人",
    "role.productOwner": "产品负责人",
    "role.legalOwner": "法务负责人",
    "role.financeOwner": "财务负责人",
    "role.deterministicControl": "确定性控制面",
    "role.changedSourceOwner": "变更源负责人",
    "role.boundedRuntime": "受限运行时",
    "role.canonicalWriter": "规范状态写者",
    "role.exactApprovalOwner": "精确批准负责人",
    "business.organization.evergreen": "常青工业",
    "business.customer.blueHarbor": "蓝港客户",
    "business.skill.quoteCompose": "企业报价组装 Skill",
    "business.role.quoteOperator": "报价运营负责人",
    "business.role.productOwner": "常青产品负责人",
    "business.role.financeOwner": "常青财务负责人",
    "business.role.skillSteward": "Skill 治理负责人",
    "business.role.controlPlane": "OrgRebase 确定性控制面",
    "business.role.readOnlyEvidence": "运行记录只读视图",
    "business.role.packAdmission": "报价包入场控制",
    "business.role.workspaceFormation": "工作区形成控制",
    "business.role.impactControl": "变更影响控制",
    "business.role.productSteward": "产品领域执行者",
    "business.role.legalSteward": "法务领域执行者",
    "business.role.financeSteward": "财务领域执行者",
    "business.role.gtmSteward": "市场与商业化领域执行者",
    "business.role.changeCoordinator": "变更协调器",
    "business.role.quoteReviewer": "报价审查智能体",
    "business.role.independentReviewer": "独立审查智能体",
    "business.role.domainCandidate": "领域候选执行者",
    "business.role.exactDomainOwner": "当前变更的领域负责人",
    "business.actor.productStewardLocal": "产品领域执行者 · 受控本地",
    "business.actor.legalStewardLocal": "法务领域执行者 · 受控本地",
    "business.actor.financeStewardLocal": "财务领域执行者 · 受控本地",
    "business.actor.gtmStewardLocal": "市场与商业化领域执行者 · 本地验证",
    "authority.stateStoreOnlyCode": "OrgRebase 规范状态控制面是唯一写入权威",
    "business.quote.blueHarbor": "蓝港企业报价",
    "business.scenario.evergreen": "常青工业企业报价持续演化",
    "business.plan.evergreenPlus": "常青企业增强版",
    "business.residency.usEu": "支持美国与欧盟区域驻留",
    "business.priceBand.strategic": "战略客户价格带",
    "business.terms.legalReview": "需法务审核",
    "business.deliverable.enterpriseQuote": "企业报价",
    "business.impact.financeApproval": "企业报价财务批准",
    "business.impact.launchReadiness": "企业上线就绪审查",
    "business.impact.partnerBrief": "企业合作伙伴简报",
    "business.change.launchDate": "产品上线日期变更",
    "business.change.currency": "财务币种政策变更",
    "business.change.generic": "业务变更",
    "business.reason.coverageNoPath": "依赖覆盖完整，未发现被采纳的影响路径",
    "business.reason.insufficientCoverage": "依赖证据不足，必须人工复核",
    "business.reason.admittedPath": "已找到可验证的依赖路径",
    "business.reason.digestMismatch": "结果摘要不匹配",
    "business.reason.staleAttempt": "陈旧尝试已隔离",
    "business.relation.requiresClaim": "依赖产品声明",
    "business.relation.requiresPolicy": "依赖财务政策",
    "business.evidence.syntheticGold": "受控演示基准",
    "business.evidence.localProduct": "本地产品路径观测",
    "machine.model.deterministicManager": "无模型 · 确定性控制面协调器",
    "machine.model.deterministicWorker": "无模型 · 确定性领域执行智能体",
    "machine.model.vertexAdvisoryBoundary": "真实 Vertex 结构化建议 · 非验收权威 · 无规范写权",
    "machine.mode.nativeTaskflow": "受控本地原生任务流",
    "machine.mode.goldenE2E": "本地受控业务端到端运行",
    "machine.mode.localApproval": "本地显式工作区批准",
    "machine.mode.deterministicControl": "确定性控制面",
    "machine.mode.governedSuccessor": "受控本地治理后继运行",
    "machine.mode.notExternalValidation": "非外部人工验证",
    "machine.tool.dependencyEvidence": "依赖证据工具 · 受控本地真实 HTTP",
    "machine.tool.stateStoreTransaction": "OrgRebase 规范状态原子事务",
    "machine.tool.sqliteEventTransaction": "SQLite 事务 + 事件链",
    "machine.skill.noneAtWrite": "规范写入边界不调用 Skill",
    "machine.trace.otlpCorrelated": "OTLP 已关联 · 追踪标识未投影",
    "machine.project.notObserved": "项目节点未观测",
    "machine.tool.controlledHttp": "受控 HTTP 工具",
    "machine.skill.upstreamCandidate": "企业报价组装 Skill 位于候选生成上游",
    "machine.deployment.controlledLocal": "受控本地部署档案（初版）",
    "machine.tool.vertex37AdvisoryVerifier": "vertex-ai:gemini-3.7-flash 建议 + 确定性校验器",
    "machine.tool.vertex38AdvisoryVerifier": "vertex-ai:gemini-3.8-flash 建议 + 确定性校验器",
    "system.crmCpqQueue": "CRM / CPQ 请求队列",
    "system.productSource": "产品目录 / 发布源",
    "system.legalSource": "CLM / 受限法务源",
    "system.financeSource": "ERP / 定价政策源",
    "system.quoteComposer": "文档 / CPQ 报价组装器",
    "system.quoteOutput": "CPQ / 文档输出",
    "system.manualReview": "跨系统手工变更复核",
    "system.manualRework": "文档与系统手工返工",
    "unit.minute": "分钟",
    "unit.businessHour": "个工作小时",
    "unit.businessDay": "个工作日",
    "spine.pack": "报价包入场",
    "spine.source": "数据源",
    "spine.agentTeams": "AgentTeams",
    "spine.skill": "Skill",
    "spine.governedApply": "受治理写入",
    "spine.workers": "领域执行智能体",
    "spine.review": "独立复核",
    "spine.formation": "工作区形成",
    "spine.human": "人工门禁",
    "spine.rebase": "选择性重构",
    "spine.quote": "报价交付物",
    "spine.evidence": "证据闭环",
    "spine.preview": "影响预演",
    "spine.apply": "受治理写入",
    "spine.terminal": "运行终态",
    "spine.lifecycle": "原生生命周期",
    "spine.retained": "已保留运行记录",
    "spine.detail.synthetic": "受控演示数据 · 已声明",
    "spine.detail.nativeControl": "固定版本原生控制面",
    "spine.detail.candidateWrites": "仅候选 · 0 次目标写入",
    "spine.detail.advisory": "仅供参考",
    "spine.detail.financeRecovery": "财务缺失事实恢复",
    "spine.detail.formationReceipt": "受控 AgentTeams 形成回执",
    "spine.detail.explicitClick": "等待指定负责人显式点击",
    "spine.detail.stateStore": "仅规范状态控制面可写",
    "spine.detail.deliverable": "企业交付物",
    "spine.detail.strictAdmission": "严格入场校验",
    "spine.detail.domainContext": "四领域上下文",
    "spine.detail.zeroWrite": "0 次写入",
    "spine.detail.validationFailed": "摘要校验失败",
    "spine.detail.failClosed": "默认拒绝，不推断",
    "field.dependsOn": "依赖任务",
    "field.inputRefs": "输入引用",
    "field.inputDigest": "输入摘要",
    "field.digest": "对象摘要",
    "field.outputDigest": "输出摘要",
    "field.model": "模型",
    "field.modelProvider": "模型提供方",
    "field.providerRequest": "提供方请求号",
    "field.modelAuthority": "模型权威边界",
    "field.tool": "工具",
    "field.skill": "Skill",
    "field.trace": "追踪标识",
    "field.candidateOnly": "是否仅候选",
    "field.targetWrites": "目标写入数",
    "field.evidenceMode": "证据 / 运行模式",
    "topology.role.workerGeneric": "执行智能体 · {domain}",
    "topology.role.controlAcceptance": "确定性验收控制 · 无独立审查智能体",
    "topology.role.humanOwner": "人工负责人",
    "topology.role.canonicalState": "规范状态",
    "topology.role.coordinator": "控制面任务协调器",
    "topology.role.projectBoundary": "协调者 / 项目边界",
    "topology.name.quoteTaskCoordinator": "报价任务协调器",
    "topology.auditIdentifier": "精确执行标识",
    "topology.role.humanCommand": "人工负责人命令边界",
    "topology.stage.coordinate": "01 · 协调与拆解",
    "topology.stage.domainWorkers": "02 · 领域执行智能体",
    "topology.stage.accept": "03 · 验收候选",
    "topology.stage.humanShort": "04 · 人工决策",
    "topology.stage.write": "05 · 规范写入",
    "topology.name.formation": "受控 AgentTeams 形成器",
    "topology.name.acceptance": "OrgRebase 确定性验收控制",
    "topology.name.localCommands": "脚本化本地领域负责人命令",
    "topology.detail.declarative": "无模型 · 声明式程序",
    "topology.detail.failClosedVerifier": "无模型 · 默认拒绝校验器",
    "topology.detail.onlyWriter": "无模型 · 唯一规范写者",
    "topology.detail.deterministic": "无模型 · 确定性控制",
    "topology.detail.localCommand": "本地命令",
    "topology.detail.ownerCommand": "负责人命令",
    "topology.detail.serverWait": "服务端审阅等待 {milliseconds}ms",
    "selective.round1": "第一轮 · 协作工作稿 → 上线日期确认稿",
    "selective.round2": "第二轮 · 上线日期确认稿 → 币种确认稿",
    "selective.launchDate": "上线日期",
    "selective.currency": "币种",
    "selective.rebuilt": "已重建",
    "selective.preserved": "已保留",
    "selective.unknown": "证据不足",
    "selective.falseInvalidations": "错误失效数",
    "selective.unauthorized": "越权披露数",
    "selective.approvalDigest": "批准摘要",
    "selective.auditDetails": "查看批准回执",
    "selective.final": "{quote} · 上线 {launch} · 币种 {currency}",
    "value.process.status": "{count} 个步骤 · RACI 已映射",
    "value.target.design": "参考人工基线 · 待企业校准",
    "value.metricEvidence": "价值证据",
    "value.failClosed": "默认拒绝",
    "value.noRoiClaim": "不作 ROI 声明",
    "value.cost.detail": "归一化操作成本 · 不是企业 ROI<br>声明区间 {lower}–{upper} · 压力区间 {stressLower}–{stressUpper}",
    "retained.authority.value": "AgentTeams 候选 → {state}",
    "retained.exceptions.value": "冲突 {conflict} · 重派 {reassign} · 迟到结果隔离 {late}<br>原子回滚 {atomic} · 下游状态补偿 {downstream} · 可逆 Git 补偿 {git}<br>{enterprise} · 残留影响 {residuals} 项 · 读模型写入 {writes} 次 · 独立机制证据，不并入当前业务运行",
    "retained.exceptions.enterprisePending": "真实企业连接器补偿尚未接入",
    "retained.successor.value": "{quote} · {terminal} · {approval} · {writes} 次规范写入",
    "retained.skill.header": "Skill {version} · {verdict}",
    "retained.skill.evaluation": "{cases} 个评测分区 · {passed}/{total} 个准入门槛通过 · {mode} · 发布回执已绑定",
    "retained.skill.requal": "重验 {verdict} · {state} / {mode}",
    "retained.skill.invocation": "已发现 → Python Wheel 包已装载 → 已调用并生成回执",
    "retained.skill.technical": "查看技术回执",
    "retained.skill.unavailable": "本次任务未绑定独立的 Skill 发布生命周期汇总；下方已发布原文直接来自能力注册表。",
    "retained.ledger.count": "{actions} 个操作 · {bindings} 个绑定",
    "retained.ledger.fail": "默认拒绝",
    "retained.boundary.value": "已验证 · 独立本地运行 · 固定版本进程内 · 非实时分布式",
    "retained.runLabel": "独立运行已验证",
    "operations.card.alert": "告警规则验证",
    "operations.card.retention": "留存清理演练",
    "operations.card.query": "OTLP 留存查询",
    "operations.card.capacity": "本机顺序容量基础验证",
    "operations.card.backup": "单机静止备份 / 恢复",
    "operations.card.recovery": "独立运行 · SIGKILL 恢复",
    "operations.sameRun.runLabel": "同一运行证据已关联",
    "operations.card.receiptBound": "审计回执已绑定",
    "operations.card.alerts": "{count} 个告警",
    "operations.card.alertsVerified": "健康链已通过（当前告警 {healthy} 个） · 故障探针捕获 {negative} 个 {severity} · 外部送达需部署时配置",
    "operations.card.retentionDetail": "删除 {deleted} 条过期测试记录 · 业务证据保留 · 长周期执行待验证",
    "operations.card.queryDetail": "单机 SQLite 中的追踪 / 日志 / 指标",
    "operations.card.capacityDetail": "{successes} 次顺序调用成功 · 第 95 百分位耗时 {p95}ms · 非并发 / 非 SLA",
    "operations.card.backupDetail": "{count} 个本机制品 · 摘要{match} · 非异地灾备",
    "operations.card.localVerified": "本地验证完成",
    "operations.card.singleHostVerified": "单机恢复完成",
    "operations.card.completedStateRecovered": "两次受控本地恢复通过",
    "operations.card.match": "匹配",
    "operations.card.unknown": "未知",
    "operations.card.recoveryDetail": "{count} 次 SIGKILL · 独立受控本地 · {dispositions} · 自主分布式恢复需生产环境验收",
    "operations.integrations": "本地数据源 HTTP {source} · 本地工具 HTTP {tool} · 0 次外部目标写入 · 企业系统连接器需部署时配置",
    "operations.sameRun.externalTargetWrites": "{count} 次外部目标写入",
    "operations.sameRun.externalTargetWritesUnobserved": "外部目标写入数未单独观测",
    "operations.sameRun.internalCanonicalWrite": "内部规范状态已写入",
    "plane.control": "控制面",
    "operations.otlp.summary": "查询 {status} · 追踪、运行、任务、Skill、工具回执与写入数已关联 · {count} 个属性",
    "operations.readiness.enterpriseValidation": "需企业生产环境验收",
    "operations.readiness.summary": "冻结 OrgRebase {version} 制品 · {deployment} · {sbom} 锁文件组件 {components} 个 · 贡献指南 {guide} · 生产 SLA {sla} · 异地灾备 {geoDr}",
  }),
  en: Object.freeze({
    "meta.title": "OrgRebase | Governed Enterprise Work Evolution",
    "a11y.home": "OrgRebase home",
    "a11y.runtimeBoundary": "Runtime boundaries",
    "a11y.currentOperator": "Current stage actor",
    "a11y.complexityBridge": "Original workflow, engineering challenge, and OrgRebase solution",
    "a11y.currentAction": "Current permitted action",
    "a11y.evidenceViews": "Execution views",
    "a11y.activeEvidenceSpine": "Active run chain",
    "a11y.activeTopology": "Active quote-workflow Agent topology",
    "a11y.retainedEvidence": "Validated dynamic formation and control semantics",
    "a11y.retainedTopology": "Retained AgentTeams topology",
    "a11y.formationTopology": "Two-domain dynamic Formation AgentTeams topology",
    "a11y.oacAdaptationStages": "Six-stage OAC enterprise onboarding",
    "a11y.oacReviewOverview": "Organization-contract review overview",
    "a11y.oacReviewComponents": "How five enterprise-input classes form the contract",
    "a11y.oacAdaptationOutcomes": "OAC enterprise-adaptation outcomes",
    "a11y.oacSourceBindings": "Technical bindings for five enterprise-input classes",
    "language.group": "Language",
    "language.switchChinese": "Switch to Chinese",
    "language.switchEnglish": "Switch to English",
    "brand.tagline": "Governed Enterprise Work Evolution",
    "hero.title": "When facts change, work should know what is no longer valid.",
    "hero.body": "When business rules change, OrgRebase identifies affected work and coordinates domain Agents to propose corrections. The control plane updates the authoritative version after approval by the designated owner. Enterprise quoting is currently supported.",
    "operator.label": "CURRENT OPERATOR",
    "operator.name": "Quote Operations Owner",
    "operator.role": "Quote operations owner",
    "complexity.original.kicker": "01 · ORIGINAL WORKFLOW",
    "complexity.original.title": "One change crosses five system classes and five accountable roles",
    "complexity.original.body": "Facts, permissions, and versions are separated across CRM / CPQ, product sources, CLM, ERP / Pricing, and GTM assembly.",
    "complexity.engineering.kicker": "02 · ENGINEERING CHALLENGE",
    "complexity.engineering.title": "Calculate exact impact without granting Agents write authority",
    "complexity.engineering.body": "Dependencies must be provable; candidate and canonical state must stay isolated; conflicts, stale approvals, and interruptions must be recoverable.",
    "complexity.solution.title": "The organization contract and selective Rebase turn one change into a verifiable loop",
    "complexity.solution.body": "AUTO form and preview → HUMAN review and click → AUTO selective Rebase and evidence verification.",
    "journey.title": "One durable, auditable evolution timeline",
    "journey.loading": "Reading server state…",
    "journey.new": "New work item · waiting to form the internal working draft",
    "journey.restored": "Restored {stage} from the server · refresh-safe state",
    "journey.unavailable": "Workspace service unavailable",
    "timeline.quoteV1": "Admitted Quote Baseline",
    "timeline.quoteV1.detail": "HUMAN STARTS BASELINE · AGENTS AUTO-FORM CANDIDATE",
    "timeline.launch.title": "Product date change",
    "timeline.preview.detail": "UPSTREAM CHANGE ARRIVES · SYSTEM RUNS ZERO-WRITE PREVIEW",
    "timeline.approve.detail": "HUMAN · APPROVE THIS CHANGE",
    "timeline.rebase.detail": "AUTO · APPLY APPROVED CHANGE",
    "timeline.quoteV2": "Launch-date confirmed draft",
    "timeline.recovery.title": "Service recovery point",
    "timeline.recovery.detail": "AUTO · DURABLE RECOVERY",
    "timeline.currency.title": "Finance currency change",
    "event.preview": "Preview this change",
    "event.previewDetail": "Check the event against the current result and its dependencies.",
    "event.approve": "Approve this change",
    "event.approveDetail": "The owner approves the exact preview and version.",
    "event.apply": "Apply approved change",
    "event.applyDetail": "Recheck source, approval and result versions before atomic commit.",
    "event.recover": "Recover result record",
    "event.recoverDetail": "Verify the committed change and restore its original result record without another change or approval.",
    "event.reject": "Reject this change",
  "event.rejectionReason": "Reason for rejection",
  "action.openChange.label": "Review this proposal",
  "event.confirmReject": "Record rejection",
  "event.rejected": "The owner rejected this change. Submit a new change after revising it.",
  "event.reasonRequired": "Enter a reason for rejection.",
  "event.idle": "Waiting for the next change",
    "event.idleDetail": "The current result is saved and can receive further business events.",
    "event.current": "Current result",
    "event.previewed": "Change awaiting approval",
    "event.evidenceRequired": "Change awaiting evidence",
    "event.approved": "Change approved",
    "event.recoveryRequired": "Change applied; result recovery required",
    "timeline.quoteV3": "Currency confirmed draft",
    "timeline.quoteV3.detail": "AUTO · INTERNAL-CHANGE EVIDENCE VERIFIED",
    "command.next": "NEXT",
    "command.loadingTitle": "Read Workspace state",
    "command.loadingDetail": "Only the primary action permitted at the current stage is shown.",
    "command.loadingButton": "Please wait…",
    "command.unavailableTitle": "Workspace state unavailable",
    "command.unavailableDetail": "No authoritative state was loaded. The UI will not present a failed request as an empty Workspace or enable business actions.",
    "command.unavailableButton": "Waiting for service recovery",
    "command.unavailableActor": "Workspace state service",
    "command.unavailableRole": "FAIL CLOSED · PRESERVE THE LAST VERIFIED STATE",
    "command.downloadQuote": "Download work result",
    "command.downloadEvidence": "Download audit record",
    "command.reviewWait": "The server review gate is not open yet; {seconds}s remaining. The human approval button unlocks when the countdown ends.",
    "command.reviewButton": "Review preview · {seconds}s",
    "command.approveButton": "Confirm human approval",
    "command.approveLaunchButton": "Approve product date change",
    "command.approveCurrencyButton": "Approve finance currency change",
    "command.completed": "VERIFIED · INTERNAL CHANGE LOOP COMPLETE",
    "command.baseline": "QUOTE BASELINE FORMED · NO BUSINESS RULE CHANGES YET",
    "command.recorded": "RESULT RECORDED · CHECKING COMPLETION RECEIPTS",
    "header.currentTask.baseline": "ACTIVE TASK: QUOTE CREATED",
    "currentArchive.titleBaseline": "The current Quote baseline is archived",
    "currentArchive.baselineArchived": "Archived · no rule-change approvals or Rebase yet",
    "value.current.baseline.title": "Current Quote baseline",
    "value.current.baseline.detail": "The Quote is ready and no rule changes await approval. To change a rule, preview the impact and request owner approval.",
    "value.current.baseline.noChanges": "No rule-change decisions yet",
    "command.ownerReview": "HUMAN · OWNER REVIEW · {seconds}s",
    "command.ownerAction": "HUMAN · OWNER ACTION REQUIRED",
    "command.humanStart": "HUMAN STARTS BASELINE · CHANGE OWNER",
    "command.autoControl": "AUTO · DETERMINISTIC CONTROL PLANE",
    "command.oacGate": "CONTROL-PLANE DECISION · OAC BUSINESS START GATE",
    "command.oacChecking": "CHECKING · OAC BUSINESS START GATE",
    "command.experienceReview": "HUMAN · EXPERIENCE CANDIDATE REVIEW",
    "command.formRunning": "AGENT AUTO-RUN · NO TERMINAL OBSERVED",
    "command.formRunning.title": "Pinned AgentTeams is forming the internal working-draft candidate",
    "command.formRunning.detail": "Expected protocol: domain Agents submit candidates only; after Reviewer acceptance, the OrgRebase control plane may form the collaborative working draft. No terminal state has been observed yet.",
    "command.formRunning.actor": "Pinned AgentTeams + OrgRebase control plane",
    "command.formRunning.role": "Candidate orchestration / terminal acceptance · no canonical write yet",
    "command.formRunning.button": "Forming collaborative working draft…",
    "cockpit.title": "Enterprise Execution Observatory",
    "cockpit.body": "Business, collaboration, and operations share one linked execution chain. This read-only view shows actual progress without writing business state.",
    "cockpit.tab.scene": "Business & Data",
    "cockpit.tab.value": "Value & Ownership",
    "cockpit.tab.collaboration": "Agent Collaboration",
    "cockpit.tab.operations": "Operations & Failures",
    "dataJourney.aria": "Four-stage Quote task version chain",
    "dataJourney.boundaries.aria": "Evidence boundaries",
    "dataJourney.kicker": "THIS TASK · CHANGES & ACCEPTANCE",
    "dataJourney.title": "Change Impact and Quote Successor Versions",
    "dataJourney.waiting": "Waiting for admitted runtime facts",
    "dataJourney.status.ready": "{facts} enterprise facts → {slots} task slots → {version} · read-only view, 0 Agent-candidate business writes",
    "dataJourney.status.progress": "This run is still forming its data chain; only observed facts are shown, with {writes} Agent-candidate business writes",
    "dataJourney.status.waiting": "Runtime facts are not complete · fail closed · {writes} Agent-candidate business writes",
    "dataJourney.source.title": "Admitted enterprise baseline",
    "dataJourney.source.waiting": "No admitted business slots observed",
    "dataJourney.source.summary": "{count} admitted facts · {components} OAC enterprise material classes",
    "dataJourney.source.value": "CURRENT VALUE",
    "dataJourney.source.provenance": "source {source} · authority {authority}",
    "dataJourney.source.capabilityRequirement": "ENTERPRISE CAPABILITY REQUIREMENT REF · NOT THE SKILL PACKAGE LOADED IN THIS RUN",
    "dataJourney.source.unbound": "Source or authority reference not observed",
    "dataJourney.source.registeredReference": "Registered business source",
    "dataJourney.source.productCatalog": "Product catalog",
    "dataJourney.source.releasePlan": "Release plan",
    "dataJourney.source.platformCapability": "Platform capability source",
    "dataJourney.source.contractRegister": "Contract register",
    "dataJourney.source.pricingPolicy": "Pricing policy",
    "dataJourney.source.currencyPolicy": "Currency policy",
    "dataJourney.source.partnerPolicy": "Partner policy",
    "dataJourney.source.skillRegistry": "Skill registry",
    "dataJourney.source.publicMessage": "Public message source",
    "dataJourney.authority.product": "Product owner",
    "dataJourney.authority.legal": "Legal owner",
    "dataJourney.authority.finance": "Finance owner",
    "dataJourney.authority.gtm": "GTM owner",
    "dataJourney.authority.skillRegistry": "Skill registry owner",
    "dataJourney.oac.title": "OAC constraints bound",
    "dataJourney.oac.waiting": "No complete contract binding observed",
    "dataJourney.oac.summary": "{count}/5 contract classes bound · source admission {admission} · activation projection {projection}",
    "dataJourney.oac.summaryBound": "{count}/5 contract classes bound · OAC formed {domains} domain tasks · execution topology matches",
    "dataJourney.oac.summaryPending": "{count}/5 source classes admitted · organization contract awaiting activation",
    "dataJourney.oac.summaryActivated": "{count}/5 source classes admitted · contract activated and consumed by task formation · execution binding awaiting proof",
    "dataJourney.oac.binding": "source {admission} · projection {projection}",
    "dataJourney.oac.bindingPending": "source {admission} · projection {projection} · exact binding incomplete",
    "dataJourney.oac.activation": "quote precondition binding {status}",
    "dataJourney.oac.boundary": "OAC admits organization material and constrains this task and its least-privilege context; AgentTeams executes candidate tasks only, while the OrgRebase control plane owns acceptance and canonical writes.",
    "dataJourney.oac.boundaryPending": "The organization contract is not yet bound to this AgentTeams execution plan in the same run; only observed source-admission and activation facts are shown.",
    "dataJourney.context.title": "OrgRebase least-privilege context",
    "dataJourney.context.waiting": "No task projection observed",
    "dataJourney.context.summary": "{domains} domain Agents · {actors} governed recipients · {slots} task slots",
    "dataJourney.context.actor": "received {included} required slots · excluded {excluded} slots",
    "dataJourney.context.reasons": "Recorded exclusion reasons: {reasons}",
    "dataJourney.context.reason.otherDomain": "other authority domain: {count}",
    "dataJourney.context.reason.derivedOnly": "derived information only: {count}",
    "dataJourney.context.reason.forbidden": "output forbidden: {count}",
    "dataJourney.context.reason.sensitivity": "above sensitivity ceiling: {count}",
    "dataJourney.context.reason.notRequired": "not required for this task: {count}",
    "dataJourney.context.reason.other": "other reasons: {count}",
    "dataJourney.context.noSlots": "No business slots projected to this recipient",
    "dataJourney.context.authority": "Formation authority: OrgRebase control-plane task context compiler",
    "dataJourney.quotes.title": "Quote version chain",
    "dataJourney.quotes.waiting": "No internal quote revision has been formed",
    "dataJourney.quotes.summary": "{count} observed revisions · current {version}",
    "dataJourney.quotes.version": "{version} · launch {launch} · currency {currency}",
    "dataJourney.quotes.incomplete": "Revision history is incomplete; missing revisions are not fabricated",
    "dataJourney.changes.title": "Pre-approval candidate impact and post-approval selective Rebase",
    "dataJourney.changes.waiting": "No business change has been observed; no change or approval is fabricated.",
    "dataJourney.changes.launchDate": "PRODUCT LAUNCH DATE",
    "dataJourney.changes.currency": "QUOTE CURRENCY",
    "dataJourney.changes.delta": "{from} → {to}",
    "dataJourney.changes.owner": "human owner {owner} · approval {approval}",
    "dataJourney.changes.transition": "{before} → {after} · receipt {receipt}",
    "dataJourney.changes.metrics": "rebuilt {rebuilt} · evidence preserved {preserved} · human review {unknown} · false invalidations {falseInvalidations} · unauthorized disclosures {unauthorized}",
    "dataJourney.changes.notObserved": "This round's receipt is not fully observed and is not shown as completed.",
    "dataJourney.changes.previewObserved": "The VMRC exactly binds this zero-write preview. These are pre-approval candidate impacts, not applied results.",
    "dataJourney.changes.previewMetrics": "zero-write candidate: rebuild {rebuilt} · preserve evidence {preserved} · human review {unknown}",
    "dataJourney.changes.previewOwner": "human owner {owner} · approval pending (canonical writes remain 0)",
    "dataJourney.changes.previewTransition": "current version remains {before} · no successor version before approval",
    "dataJourney.boundary": "The first two stages are the admitted baseline. After a change arrives, zero-write Preview and candidate impact appear first; successor Quote versions and Rebase receipts appear only after the matching Owner approves.",
    "dataJourney.boundaries.fixture": "CONTROLLED INSTANCE: STRUCTURAL LOOP PROOF",
    "dataJourney.boundaries.public": "PUBLIC PROCESS: INDEPENDENT CROSS-PROCESS MAPPING VALIDATION",
    "dataJourney.boundaries.pilot": "ENTERPRISE PILOT: CONNECTORS AND ROI REQUIRE ENTERPRISE VALIDATION",
    "dataJourney.component.domain": "Domain",
    "dataJourney.component.knowledge": "Knowledge",
    "dataJourney.component.authority": "Authority",
    "dataJourney.component.capability": "Capability",
    "dataJourney.component.dependency": "Dependency",
    "dataJourney.slot.productPlan": "Enterprise plan",
    "dataJourney.slot.launchDate": "Launch date",
    "dataJourney.slot.dataResidency": "Data residency",
    "dataJourney.slot.noticeRequired": "Customer notice requirement",
    "dataJourney.slot.priceBand": "Price band",
    "dataJourney.slot.pricingPolicy": "Pricing policy",
    "dataJourney.slot.quoteBasket": "Quote line items",
    "dataJourney.slot.currency": "Currency",
    "dataJourney.slot.partnerTerms": "Partner terms",
    "dataJourney.slot.quoteComposeSkill": "Quote composition Skill",
    "dataJourney.slot.publicMessage": "Public message",
    "dataJourney.value.approvedSchedule": "The enterprise launch remains on the approved schedule.",
    "dataJourney.sensitivity.public": "PUBLIC",
    "dataJourney.sensitivity.internal": "INTERNAL",
    "dataJourney.sensitivity.confidential": "CONFIDENTIAL",
    "dataJourney.sensitivity.restricted": "RESTRICTED",
    "dataJourney.actor.domain": "Domain Agent",
    "dataJourney.actor.renderer": "Deterministic quote renderer",
    "dataJourney.actor.rendererRole": "Deterministic governed recipient",
    "dataJourney.verdict.admitted": "ADMITTED",
    "dataJourney.verdict.match": "MATCHED",
    "dataJourney.verdict.ready": "READY",
    "dataJourney.verdict.consumed": "CONSUMED BEFORE QUOTE FORMATION",
    "dataJourney.verdict.notUsed": "NOT USED IN THIS RUN",
    "dataJourney.verdict.notObserved": "NOT OBSERVED",
    "scene.waiting": "Waiting for task formation",
    "scene.waitingQuote": "Waiting for Quote formation",
    "scene.declaredProfile": "declared Profile",
    "scene.authority.detail": "Agents, Tools, and Skills may produce candidate results and audit records only",
    "scene.ledger.title": "Current Run Action Ledger",
    "scene.ledger.zero": "0 actions",
    "scene.ledger.waiting": "Waiting for run-scoped facts…",
    "scene.ledger.count": "{count} actions · run-scoped",
    "businessChange.kicker": "CANDIDATE IMPACT BEFORE APPROVAL · SUCCESSOR VERSION AFTER APPROVAL",
    "businessChange.title": "Preview and Apply are distinct stages of the same change",
    "businessChange.waiting": "Waiting for a change run",
    "businessChange.versionPath": "{path} · current work status: {stage}",
    "businessChange.round": "ROUND {round}",
    "businessChange.launchDate": "PRODUCT LAUNCH DATE",
    "businessChange.currency": "QUOTE CURRENCY",
    "businessChange.agents": "COORDINATOR AND PARTICIPATING AGENTS",
    "businessChange.owner": "HUMAN OWNER",
    "businessChange.reviewPassed": "SERVER REVIEW GATE SATISFIED",
    "businessChange.reviewPending": "CANDIDATE AUTO-FORMED · 0 CANONICAL WRITES · MATCHING OWNER PENDING",
    "businessChange.rebuilt": "REBUILT",
    "businessChange.preserved": "EVIDENCE PRESERVED",
    "businessChange.unknown": "HELD FOR HUMAN REVIEW",
    "businessChange.falseInvalidations": "FALSE INVALIDATIONS",
    "businessChange.unauthorized": "UNAUTHORIZED DISCLOSURES",
    "businessChange.object.applied": "ACTUALLY REBUILT BY SELECTIVE REBASE",
    "businessChange.object.preserved": "EXPLICITLY UNAFFECTED",
    "businessChange.object.review": "INSUFFICIENT EVIDENCE · HUMAN REVIEW",
    "businessChange.object.candidateRebuild": "CANDIDATE REBUILD",
    "businessChange.object.candidatePreserve": "CANDIDATE EVIDENCE PRESERVATION",
    "businessChange.object.candidateReview": "CANDIDATE HUMAN REVIEW",
    "businessChange.object.change": "{object} · {from} → {to}",
    "businessChange.object.state": "{object} · {state}",
    "businessChange.object.none": "No object receipt observed",
    "businessChange.totalTitle": "The system did not redo everything",
    "businessChange.totalSummary": "Across two runs: rebuilt only {rebuilt}, preserved evidence for {preserved}, and stopped {unknown} for human review because evidence was insufficient.",
    "businessChange.totalSecurity": "false invalidations {falseInvalidations} · unauthorized disclosures {unauthorized} · totals are summed across two runs, not deduplicated objects.",
    "businessChange.progressSummary": "{previewed}/2 VMRC candidate impacts observed; {completed}/2 changes applied. A preview is never presented as a completed receipt.",
    "businessChange.previewObserved": "The VMRC exactly binds this zero-write preview; only the responsible owner can authorize Apply.",
    "businessChange.notObserved": "No receipt was observed for this round; no result was fabricated.",
    "businessChange.reviewAction": "Return to the responsible owner approval",
    "businessChange.archiveAction": "View this run's archive",
    "currentChange.kicker": "CURRENT CHANGESET · LOCAL_DETERMINISTIC AGENTRUN",
    "currentChange.title": "The current change projects a minimum team; it does not rerun Quote v1 Formation",
    "currentChange.waiting": "Waiting for the current ChangeSet Preview",
    "currentChange.invalid": "A ChangeSet exists, but its run, Preview, Owner, Handoff, or zero-write binding is incomplete; the minimum team is withheld.",
    "currentChange.summary": "{rounds} ChangeSet round(s) observed · candidate canonical writes {writes}",
    "currentChange.round": "ROUND {round} · {change}",
    "currentChange.team": "MINIMUM change_team",
    "currentChange.owner": "EXACT OWNER",
    "currentChange.writes": "CANDIDATE CANONICAL WRITES",
    "currentChange.agent": "AGENT",
    "currentChange.candidate": "CANDIDATE",
    "currentChange.capability": "AGENTRUN CAPABILITY VERSION REFS (NOT INVOCATIONS)",
    "currentChange.handoff": "HANDOFF",
    "currentChange.review": "DETERMINISTIC CANDIDATE ADMISSION (NO REVIEWER TASK)",
    "currentChange.candidateValue": "{kind} · {digest}",
    "currentChange.capabilityValue": "Tool ref {tools} · Skill ref {skills}",
    "currentChange.handoffValue": "{from} → {to} · {digest}",
    "currentChange.reviewValue": "NO NEW REVIEWER TASK · {decision} · {verifier}",
    "currentChange.workValue": "Work: {purpose} · scope {scope} · object {object}",
    "currentChange.noTool": "No external Tool recorded (proposal-only)",
    "currentChange.noSkill": "No Skill version recorded",
    "currentChange.noHandoff": "No Handoff receipt observed",
    "currentChange.noReview": "No candidate-admission receipt observed",
    "currentChange.boundary": "Tool / Skill values are capability-version refs recorded in AgentRun; a Skill ref is not presented as a loaded invocation. The current change round creates no Reviewer Task; acceptance comes from the deterministic candidate-admission receipt. Every candidate has 0 canonical writes.",
    "authorityLadder.kicker": "FORMATION AND THIS CHANGE HAVE DISTINCT EVIDENCE",
    "authorityLadder.changeTitle": "One change, from proposal to committed result",
    "authorityLadder.phase.complete": "Recorded",
    "authorityLadder.phase.current": "In progress",
    "authorityLadder.phase.waiting": "Awaiting evidence",
    "authorityLadder.phase.blocked": "Stopped",
    "authorityLadder.phase.unobserved": "Not observed",
    "businessValue.pricingPolicy": "Order discount {discount} · Tax rate {tax} · {label}",
    "businessValue.quoteBasket": "{count} line items · {currency}",
    "quotePricing.title": "Quote amounts",
    "quotePricing.caption": "Calculated amounts in the current committed version",
    "quotePricing.item": "Item",
    "quotePricing.quantity": "Quantity",
    "quotePricing.unitPrice": "Unit price",
    "quotePricing.lineAmount": "Line amount",
    "quotePricing.lineId": "Line ID {id}",
    "quotePricing.subtotal": "Subtotal",
    "quotePricing.discount": "Discount ({rate})",
    "quotePricing.net": "Net amount",
    "quotePricing.tax": "Tax",
    "quotePricing.total": "Total",
    "quotePricing.sources": "Amount sources and calculation basis",
    "quotePricing.basketSource": "Basket source",
    "quotePricing.policySource": "Policy source",
    "quotePricing.rounding": "Rounding",
    "quotePricing.basketDigest": "Basket digest",
    "quotePricing.policyDigest": "Policy digest",
    "quotePricing.boundary": "Amounts use this version's basket and policy; taxes and discounts follow the listed policy source. Historical samples or controlled demo rules do not establish current offers or historical actual taxes.",
    "authorityLadder.title": "From team delivery to rules taking effect",
    "authorityLadder.at": "Formation · AgentTeams completed",
    "authorityLadder.candidate": "This change · Candidate admitted",
    "authorityLadder.reviewer": "Formation · Reviewer accepted",
    "authorityLadder.human": "This change · Human approved",
    "authorityLadder.canonical": "This change · Canonical state applied",
    "authorityLadder.atDetail": "{actions} native AgentTeams actions · project terminal {terminal}",
    "authorityLadder.candidateDetail": "The control plane locked this impact Preview · candidate-layer writes 0",
    "authorityLadder.reviewerDetail": "Review {attempt}: {verdict} · deterministic verifier · target writes {writes}",
    "authorityLadder.humanDetail": "{owner} · server review gate satisfied",
    "authorityLadder.canonicalDetail": "This proposal committed {quote}; current Quote {current}",
    "authorityLadder.context": "Workspace's active or latest proposal: {event} · {status}. Formation is run context; it does not replace this proposal's admission, approval, or application receipts.",
    "authorityLadder.selectedContext": "Selected proposal: {event} · {status}. Change receipts below refer to the proposal selected above; formation records provide run context only.",
    "authorityLadder.noChange": "No change proposals yet. Propose a rule update to track its approval and application here.",
    "authorityLadder.waiting": "No independent receipt observed for this level",
    "authorityLadder.observed": "RECEIPT OBSERVED",
    "technicalEvidence.title": "TECHNICAL EVIDENCE DETAILS",
    "technicalEvidence.sceneSummary": "run identifier, candidate chain, and same-run action ledger",
    "table.type": "TYPE",
    "table.actor": "ACTOR",
    "table.action": "ACTION",
    "table.status": "STATUS",
    "table.writes": "WRITES",
    "table.receipt": "RECEIPT",
    "value.boundary.initial": "The eight-step flow compares the AS-IS human baseline and RACI. System runtime and human waiting must be read separately.",
    "value.process.title": "Eight-step Quote Responsibility Chain",
    "value.table.step": "STEP",
    "value.table.system": "AS-IS SYSTEM CLASS",
    "value.table.asIsResponsible": "AS-IS HUMAN EXECUTOR (R)",
    "value.table.toBeResponsible": "POST-CHANGE EXECUTOR",
    "value.table.accountable": "HUMAN ACCOUNTABILITY & APPROVAL (A)",
    "value.table.target": "REFERENCE HUMAN BASELINE",
    "value.table.connector": "CONNECTOR",
    "value.connector.controlledLocal": "CONTROLLED-LOCAL SOURCE / TOOL",
    "value.connector.enterprisePending": "REAL ENTERPRISE CONNECTOR NOT CONNECTED",
    "value.raci.legend": "AS-IS R is the current human executor; the post-change executor is an Agent or governed system; A remains the human owner of final accountability and approval.",
    "value.metrics.title": "Current Task Acceptance",
    "value.metrics.boundary": "Only verified receipts produced by the active run are shown",
    "value.cost.loading": "Reading historical cost-model receipt…",
    "value.primaryUnavailable": "Evidence unavailable · fail closed",
    "value.boundary.valid": "Current validation slice: {deliverable}. Table timings are reference human baselines pending enterprise calibration; system measurements come from run receipts, and human waiting is shown at approval gates.",
    "value.boundary.invalid": "Value-assessment record validation failed; no ownership or value was inferred.",
    "value.process.unavailable": "Responsibility baseline unavailable; nothing was fabricated.",
    "value.metric.unavailable": "Not inferred or fabricated.",
    "value.cost.summary": "{full} full → {selective} selective → {saving} modelled saving",
    "value.cost.unavailable": "Value receipt unavailable",
    "value.current.waiting.label": "ACTIVE RUN",
    "value.current.waiting.value": "WAITING FOR TASK COMPLETION",
    "value.current.waiting.detail": "Results appear only after this run produces and validates them",
    "value.current.waiting.evidence": "stage {stage} · no historical baseline fallback",
    "value.current.invalid.value": "RESULT UNAVAILABLE",
    "value.current.invalid.detail": "The run is terminal, but same-run receipt binding did not validate",
    "value.current.invalid.evidence": "FAIL CLOSED · FROZEN VALUES NOT USED AS FALLBACK",
    "value.current.metric.quote": "FINAL INTERNAL REVISION",
    "value.current.metric.quote.detail": "The current internal revision is bound to the final receipt",
    "value.current.metric.receipts": "CHANGE RECEIPTS",
    "value.current.metric.receipts.detail": "Completed Rebase receipts belong to the active run",
    "value.current.metric.approvals": "OWNER APPROVALS",
    "value.current.metric.approvals.detail": "Verified approvals for the actual source scopes in this archive",
    "value.current.summary.detail": "The full archive is verified. This page loads recent changes only; work-item details, time, money, and ROI are not calculated.",
    "value.current.summary.label": "WORK-ITEM DETAILS NOT CALCULATED",
    "value.current.metric.decisions": "IMPACT DECISIONS",
    "value.current.metric.decisions.detail": "Target decisions across applied changes",
    "value.current.metric.rebuilt": "ACTUALLY REBUILT",
    "value.current.metric.rebuilt.detail": "Only work proven affected was rebuilt",
    "value.current.metric.preserved": "EVIDENCE PRESERVED",
    "value.current.metric.preserved.detail": "Still valid within the declared boundary",
    "value.current.metric.held": "HELD FOR HUMAN REVIEW",
    "value.current.metric.held.detail": "Stop on insufficient evidence; do not guess",
    "value.current.metric.safety": "SAFETY EXCEPTIONS",
    "value.current.metric.safety.detail": "false invalidations {falseInvalidations} · unauthorized disclosures {unauthorized}",
    "value.current.metric.evidence": "ACTIVE RUN · VERIFIED RECEIPT",
    "value.current.cost.label": "ACTIVE-RUN STRUCTURAL RESULT",
    "value.current.cost.summary": "{decisions} impact decisions → {rebuilt} actual rebuilds",
    "value.current.cost.detail": "Observed receipts: preserved {preserved} · human review {unknown} · false invalidations {falseInvalidations} · unauthorized disclosures {unauthorized}<br>Not converted into money, labor time, or enterprise ROI",
    "value.current.cost.modelledLabel": "COMPLETED-STATE MODELLED COST COMPARISON",
    "value.current.cost.modelledSummary": "{full} full → {selective} selective → {saving} modelled saving",
    "value.current.cost.modelledDetail": "Declared range {lower}–{upper} · stress range {stressLower}–{stressUpper}<br>Independently verified value receipt · normalized action cost · not measured in this run, hours, or money · not real-enterprise ROI",
    "value.current.cost.waiting": "WAITING FOR THE ACTIVE TASK TO COMPLETE",
    "value.current.cost.waitingDetail": "Reference cost models are not results from this task. Cycle time, labor, rework, and ROI require an enterprise observation window.",
    "proofOverview.kicker": "HISTORICAL RELEASE VERIFICATION",
    "proofOverview.title": "Browse verification by capability",
    "proofOverview.boundary": "These are historical records from separate runs. See the current task record above.",
    "proofOverview.loading": "VALIDATING EVIDENCE…",
    "proofOverview.pass": "VERIFIED",
    "proofOverview.historical": "HISTORICAL · NOT CURRENT BUILD QUALIFICATION",
    "proofOverview.historicalBuild": "These results belong to the retained historical artifact. They do not rebuild or qualify the current version.",
    "proofOverview.unavailable": "EVIDENCE UNAVAILABLE",
    "proofOverview.waitingOac": "AWAITING THIS ONBOARDING ADMISSION",
    "proofOverview.golden.title": "Quote workflow · Historical run",
    "proofOverview.golden.detail": "OAC → AgentTeams → Tool / Skill → payload-verified human gates → selective Rebase → completed-state replay",
    "proofOverview.oac.title": "OAC enterprise adaptation",
    "proofOverview.oac.detail": "Declared input classes, Unknowns, owner admission, and exact activation binding",
    "proofOverview.bpi.title": "Public real-world process",
    "proofOverview.bpi.detail": "Scope replay over the real anonymized BPI 2019 purchase-to-pay log",
    "proofOverview.formation.title": "On-demand dynamic formation",
    "proofOverview.formation.detail": "Different requests form different domain-Agent and Reviewer topologies",
    "proofOverview.agentic.title": "Agent-proposed adaptation",
    "proofOverview.agentic.detail": "An Agent proposes BPI→OAC mapping while deterministic control preserves authority gaps",
    "proofOverview.product.kicker": "PRODUCT PATH REGRESSION VALIDATION · NOT A SIXTH BUSINESS CHAIN",
    "proofOverview.product.title": "Real HTTP entrypoint, attack variants, and process restarts",
    "proofOverview.product.loading": "Loading the current product-path verification receipt…",
    "proofOverview.product.detail": "{casesPassed}/{caseCount} paths · {mutationsKilled}/{mutationCount} mutations · {integrityRejected}/{integrityTotal} integrity attacks · {gateRejected}/{gateTotal} admission attacks · {processes} real service processes · {restarts} real restarts",
    "currentArchive.kicker": "THIS-RUN ARCHIVE · COMPLETION STATE",
    "currentArchive.title": "View run records here after the task completes",
    "currentArchive.titleReady": "Task archived",
    "currentArchive.description": "Review this task’s team execution, human approvals, version updates, and delivered result.",
    "currentArchive.pending": "AWAITING CURRENT TASK COMPLETION",
    "currentArchive.loading": "VERIFYING SAME-RUN COMPLETION RECEIPTS…",
    "currentArchive.archived": "Archived",
    "currentArchive.invalid": "ARCHIVE CLOSURE INVALID · FAIL CLOSED",
    "currentArchive.unavailable": "COMPLETION ARCHIVE UNAVAILABLE",
    "currentArchive.retry": "Read evidence again",
    "currentArchive.retryBoundary": "Read only the current run's unavailable archive or observability evidence. Tasks and approvals are not repeated.",
    "currentArchive.runId": "CURRENT RUN_ID",
    "currentArchive.sameRun": "Identical to the just-completed quote task",
    "currentArchive.finalQuote": "FINAL DELIVERY",
    "currentArchive.receipts": "Approvals and updates",
    "currentArchive.receiptsValue": "{approvals} human approvals · {rebases} Rebase operations",
    "currentArchive.receiptsDetail": "Completion records for this task",
    "currentArchive.authority": "Record authority",
    "currentArchive.authorityDetail": "Agent, Tool, and Skill cannot write this archive conclusion",
    "currentArchive.fields": "Launch {launch} · currency {currency}",
    "currentArchive.boundary": "Task identity, approvals, versions, and the event chain are checked before archiving. Historical verification records are stored separately.",
    "archive.frozen.kicker": "RELIABILITY VERIFICATION · HISTORICAL RECORDS",
    "archive.frozen.title": "Historical verification records",
    "archive.frozen.boundary": "Review results and sources under their original run identities, stored separately from the current task.",
    "publicValidation.kicker": "PUBLIC DATA CASE · BPI 2019",
    "publicValidation.title": "Which orders are affected by a procurement rule change?",
    "publicValidation.loading": "Reading receipts…",
    "publicValidation.runId": "PUBLIC-PROCESS MECHANISM-VALIDATION RUN",
    "publicValidation.runSummary": "SEPARATE run_id · {runId}",
    "publicValidation.archiveStatus": "ARCHIVE STATUS",
    "publicValidation.archiveFrozen": "FROZEN · NOT THE CURRENT QUOTE TASK",
    "publicValidation.archiveTime": "ORIGINAL ARCHIVE DIGEST",
    "publicValidation.adaptationRunId": "OAC CANDIDATE-ADAPTATION RUN",
    "publicValidation.executionRunId": "TYPED-SCOPE EXECUTION RUN",
    "publicValidation.pass": "INDEPENDENT REPLAY PASS",
    "publicValidation.unavailable": "NOT PRODUCED",
    "publicValidation.fail": "RECEIPT VALIDATION FAILED",
    "publicValidation.description": "A case using real anonymized BPI 2019 procurement logs: compare the scope and provenance of rule-change impacts.",
    "publicValidation.descriptionWithSource": "{source}; a procurement rule-change scope analysis case.",
    "publicValidation.method": "official real log → digest-pinned projection → OAC typed scope → independent offline replay",
    "publicValidation.adaptation.aria": "OAC adaptation add-on over the real public BPI process",
    "publicValidation.adaptation.kicker": "BPI → OAC ADD-ON VALIDATION",
    "publicValidation.adaptation.title": "AGENT-PROPOSED MAPPING WITH DETERMINISTIC ADMISSION",
    "publicValidation.adaptation.runBoundary": "Candidate adaptation and typed-scope execution are two separate runs; neither is merged into the BPI scope-baseline run_id.",
    "publicValidation.adaptation.runRelation": "RUN RELATION",
    "publicValidation.adaptation.runRelationValue": "SEPARATE EVIDENCE · RUN IDS NOT MERGED",
    "publicValidation.adaptation.method": "public real log → governed AgentTeams candidate mapping → deterministic OAC validation → owner review gate → 128-query execution → independent replay",
    "publicValidation.adaptation.mapper": "CANDIDATE MAPPING",
    "publicValidation.adaptation.mapperValue": "{model} frozen live-run candidate receipt · {provider} · {evidence} · {count} native AgentTeams actions",
    "publicValidation.adaptation.validator": "DETERMINISTIC OAC VALIDATION",
    "publicValidation.adaptation.validatorPass": "PASS · CANDIDATE HAS NO WRITE AUTHORITY",
    "publicValidation.adaptation.unknowns": "ENTERPRISE-FACT GAPS",
    "publicValidation.adaptation.unknownValue": "{count} facts must be declared by the enterprise: {dimensions}",
    "publicValidation.adaptation.unknownApprovalAuthorities": "approval authorities",
    "publicValidation.adaptation.unknownOrganizationValues": "organization values",
    "publicValidation.adaptation.unknownPermissions": "permissions",
    "publicValidation.adaptation.unknownResponsibleOwners": "real responsible owners",
    "publicValidation.adaptation.liveModel": "LIVE MODEL REQUEST OBSERVED",
    "publicValidation.adaptation.gate": "CONTROLLED-LOCAL APPROVAL WAIT GATE",
    "publicValidation.adaptation.gateValue": "early scripted command rejected · admitted after {elapsed} seconds",
    "publicValidation.adaptation.queries": "TYPED-SCOPE EXECUTION",
    "publicValidation.adaptation.queryValue": "{count} fixed queries",
    "publicValidation.adaptation.recall": "INDEPENDENT RULE-REPLAY RECALL · NOT MODEL ACCURACY",
    "publicValidation.adaptation.falseUnaffected": "FALSELY MARKED UNAFFECTED",
    "publicValidation.adaptation.writes": "CANONICAL-STATE WRITES",
    "publicValidation.adaptation.boundary": "BPI purchase-to-pay add-on: a real public log can produce a constrained OAC candidate through an Agent and drive typed-scope replay. This is not quote data, enterprise ROI, production deployment, or proof of automatic adaptation to any enterprise. The owner gate used a controlled-local command; external-human acceptance was not proven.",
    "publicValidation.adaptation.unavailable": "Candidate mapping records are unavailable. Check evidence package integrity.",
    "publicValidation.boundaryAdapted": "The public-real-log, Agent candidate-mapping, deterministic OAC-validation, and 128-query replay chain is closed. The task-rule decision basis is query-contract derived: not human causal annotation, not quote data, and not ROI evidence.",
    "publicValidation.unavailableDescription": "This package does not contain readable BPI procurement records.",
    "publicValidation.failDescription": "Record integrity checks failed. Check or reinstall the evidence package.",
    "publicValidation.traces": "PURCHASE-ORDER-ITEM TRACES",
    "publicValidation.events": "EVENTS",
    "publicValidation.activities": "ACTIVITY TYPES",
    "publicValidation.queries": "FIXED CHANGE-PRICE QUERIES",
    "publicValidation.scopeAria": "Safe reduction of change-action scope",
    "publicValidation.scope.vendor": "VENDOR BROADCAST",
    "publicValidation.scope.document": "PURCHASE-DOCUMENT SCOPE",
    "publicValidation.scope.oac": "OAC TYPED SCOPE (MECHANISM VALIDATION)",
    "publicValidation.scope.baseline": "BASELINE ACTION SCOPE",
    "publicValidation.scope.reduction": "SAFE REDUCTION {value}",
    "publicValidation.recall": "QUERY-CONTRACT RECALL",
    "publicValidation.unsafe": "FALSELY MARKED UNAFFECTED",
    "publicValidation.lineage": "LINEAGE CLOSURE",
    "publicValidation.selectionWaiting": "Reading sample selection and the task-rule decision basis…",
    "publicValidation.selection": "{eligible} eligible queries → {selected} digest-pinned selections · task-rule decision basis: query-contract derived and strategy-blind",
    "publicValidation.boundary": "Validated: typed scope, conservative abstention, and provenance. Not yet validated: Agent-generated OAC from BPI data bound to an OrgRebase run. The task-rule decision basis is not human causal annotation, not quote data, and not ROI evidence.",
    "publicValidation.details": "Data sources, licensing, and verification scope",
    "publicValidation.source": "OFFICIAL DATASET",
    "publicValidation.license": "LICENSE",
    "publicValidation.rawHash": "RAW FILE SHA-256",
    "publicValidation.receipt": "BENCHMARK RECEIPT",
    "publicValidation.verification": "INDEPENDENT REPLAY RECEIPT",
    "publicValidation.redistribution": "The raw XES is not redistributed with the code package. The deliverable retains a reproducible fixed projection, integrity verification, and CC BY 4.0 attribution.",
    "process.intake": "Demand intake and admission",
    "process.product": "Product capability and launch confirmation",
    "process.legal": "Legal obligation confirmation",
    "process.finance": "Pricing and currency policy confirmation",
    "process.compose": "Quote composition and evidence binding",
    "process.accept": "Customer-delivery acceptance",
    "process.impact": "Change-impact preview",
    "process.update": "Approved selective update",
    "metric.cycle": "Quote cycle time",
    "metric.policy": "Active policy-confirmation time",
    "metric.mismatch": "Exact-impact mismatch case rate",
    "metric.rework": "First-pass rework rate",
    "metric.access": "Unauthorized-access success rate",
    "metric.review": "Hold-for-review decision rate",
    "metric.saving": "Selective Rebase cost saving",
    "metric.detail.mismatch": "{numerator}/{denominator} synthetic cases · not a real missed-change rate",
    "metric.detail.security": "{numerator}/{denominator} synthetic security cases",
    "metric.detail.review": "{numerator}/{denominator} local decisions · not a workforce baseline",
    "metric.detail.cost": "{full} → {selective} normalized cost · NOT ROI",
    "metric.detail.noShadow": "No same-enterprise Shadow observation window yet",
    "collaboration.waiting.title": "Waiting to start the change-driven case",
    "collaboration.waiting.detail": "After Quote formation, this view shows the control-plane task coordinator, domain Agents, Reviewers, Tool/Skill invocations nested under their calling Agents, and control-plane records for the same run.",
    "collaboration.empty.kicker": "TEAM NOT FORMED",
    "collaboration.empty.title": "This task's Agent team is generated only after admission",
    "collaboration.empty.detail": "After OAC admission and employee-task confirmation, the system generates this run's execution plan, domain Agents, and least-privilege context.",
    "collaboration.local.active.title": "Current Quote run · governed collaboration in progress",
    "collaboration.local.active.detail": "This run has formed minimum context and candidate-control records. No native AgentTeams lifecycle or independent Reviewer receipt is bound to this run; the separate validations below are not borrowed into it.",
    "collaboration.local.complete.title": "Task completed · View collaboration records",
    "collaboration.local.complete.detail": "Inspect domain candidates, owner decisions, and version updates. Results from other verification runs are available under Run records & validation.",
    "collaboration.active.title": "Quote v1 baseline Formation replay | not a rerun for the current ChangeSet",
    "collaboration.active.detail": "AgentTeams formation, Finance evidence recovery, and the quote-compose Skill belong to the initial Quote v1 run. Later changes organize candidate tasks by affected domain; inspect each change's execution record to see whether native AT tasks actually ran.",
    "collaboration.active.model": "{suggestion} · frozen receipt; advisory only, while the deterministic verifier owns acceptance authority.",
    "agentWork.kicker": "QUOTE V1 · INITIAL FORMATION RECORDS",
    "agentWork.title": "Inspect each actor’s inputs, calls, and outputs",
    "agentWork.waiting": "Waiting for run-bound Agent receipts",
    "agentWork.controlsAria": "Agent work-detail expansion controls",
    "agentWork.expandAll": "EXPAND ALL",
    "agentWork.collapseAll": "COLLAPSE ALL",
    "agentWork.boundary": "Agents submit candidates, the Reviewer checks them, and the designated owner approves. The control plane updates the authoritative version.",
    "agentWork.viewEvolution": "CONTINUE TO BUSINESS DATA EVOLUTION",
    "agentWork.summary": "{actors} execution actors · {tasks} native AgentTeams tasks · {activities} same-run activities",
    "agentWork.fact.agents": "EXECUTION ACTORS",
    "agentWork.fact.tasks": "NATIVE TASKS",
    "agentWork.fact.writes": "AGENT / TOOL / SKILL CANONICAL WRITES",
    "agentWork.fact.value": "{count}",
    "agentWork.fact.zeroWrites": "0",
    "agentWork.card.activities": "{count} SAME-RUN ACTIVITIES",
    "agentWork.field.task": "RESPONSIBILITY",
    "agentWork.field.context": "LEAST-PRIVILEGE CONTEXT RECEIVED",
    "agentWork.field.capability": "OBSERVED TOOL / SKILL / RUNTIME CAPABILITY",
    "agentWork.field.output": "CANDIDATE OR REVIEW RESULT PRODUCED",
    "agentWork.field.handoff": "HANDOFF TO",
    "agentWork.field.effect": "RELATED BUSINESS OBJECTS",
    "agentWork.field.timeline": "RECEIPT TIMELINE",
    "agentWork.context": "received {included} required fields, excluded {excluded} fields: {slots}",
    "agentWork.context.control": "Received only the governed task graph, candidate summaries, and receipt references",
    "agentWork.noCapability": "No Tool or Skill invocation observed for this Agent",
    "agentWork.noLoadedSkill": "No loaded Skill invocation observed (not inferred from role configuration)",
    "agentWork.noCandidate": "No candidate business field observed",
    "agentWork.candidateUnavailable": "No candidate summary was observed that cross-validates against the native Task, Handoff, and subprocess receipt",
    "agentWork.candidateVerified": "VERIFIED CANDIDATE SUMMARY · INDEPENDENT PROCESS OUTPUT MATCHES TASK / HANDOFF / RECEIPT DIGESTS",
    "agentWork.candidateBoundary": "Candidate output · Not in effect",
    "agentWork.candidateAttempt": "A{attempt} · {status}",
    "agentWork.candidateProvenance": "source {source} · candidate version {version}",
    "agentWork.candidateMissing": "missing in this attempt: {fields}",
    "agentWork.candidateReasons": "reasons: {reasons}",
    "agentWork.candidateTransformation": "minimum derivation: {transformation}",
    "agentWork.reviewVerdict": "REVIEW VERDICT {verdict}",
    "agentWork.reviewMissing": "gap domains: {domains}",
    "agentWork.executorRef": "executor {executor}",
    "agentWork.capability.businessRecoveryTool": "BUSINESS EVIDENCE-RECOVERY TOOL",
    "agentWork.capability.businessRecoveryTool.detail": "Finance A2's independent Worker recovered price_band through controlled-local real HTTP",
    "agentWork.capability.businessRecoveryBinding": "BUSINESS EVIDENCE-RECOVERY RUNTIME BINDING",
    "agentWork.capability.postFormationAuditTool": "POST-FORMATION DEPENDENCY-AUDIT TOOL",
    "agentWork.capability.postFormationAuditTool.detail": "WorkspaceService read the already-formed, read-only dependency graph during Quote v1 commit. gtm-steward is the receipt actor, not an autonomous GTM-process invocation claim",
    "agentWork.capability.loadedSkill": "ACTUALLY LOADED / EVALUATED / INVOKED SKILL PACKAGE",
    "agentWork.capability.advisorySkillRef": "CHANGE-ADVISORY SKILL REF · NOT A LOADED-PACKAGE INVOCATION",
    "agentWork.capability.runtime": "AGENTTEAMS RUNTIME CAPABILITY",
    "agentWork.candidateOnly": "Candidate permissions · No authority to write canonical state",
    "agentWork.taskReceipt": "{task} · attempt {attempt} · {status}",
    "agentWork.reviewProcess": "Independent executor process recorded",
    "agentWork.modelAdvice.true": "Model advice accepted",
    "agentWork.modelAdvice.false": "Model advice not accepted",
    "agentWork.review.model": "Model advice",
    "agentWork.review.deterministic": "Deterministic review",
    "agentWork.review.unknown": "Not recorded; unknown",
    "agentWork.review.missing": "Missing domains: {domains}",
    "agentWork.review.none": "None",
    "agentWork.review.authority": "The model advises; deterministic review determines candidate admission.",
    "agentWork.reviewProcessBoundary": "Human approval is recorded separately in change approval.",
    "agentWork.managerReceipt": "{task} · coordination run · {status}",
    "agentWork.handoffReceipt": "{kind} → {target} · candidate writes {writes}",
    "agentWork.timelineReceipt": "#{sequence} · {plane} · {status}",
    "agentWork.timelineEvidence": "receipt {receipt}",
    "agentWork.relatedObjects": "same-run related objects: {objects}",
    "agentWork.task.coordinator": "Organize domain tasks from the frozen OAC plan and issue a second task-graph revision when evidence is incomplete",
    "agentWork.task.product": "Check enterprise plan, launch date, and data-residency candidate facts",
    "agentWork.task.legal": "Check the customer-notice requirement and return only the minimum Legal conclusion",
    "agentWork.task.finance": "Check currency and price band; abstain on the first missing-field attempt, then deliver after recovery",
    "agentWork.task.gtm": "Check partner terms and quote capability, then compose the Quote candidate after review passes",
    "agentWork.task.reviewer": "Aggregate four domain candidates and independently check completeness, provenance, and authority boundaries",
    "agentWork.output.coordinator": "Task dependency graph and replan record",
    "agentWork.output.reviewer": "Phase 1 REPLAN: Finance price band missing; phase 2 PASS: exact field provenance and effect boundary passed",
    "agentWork.effect.coordinator": "Schedules and replans only; produces no business conclusion",
    "agentWork.effect.product": "Provides Product candidate facts required by this task; the control plane verifies affected objects and subsequent changes",
    "agentWork.effect.legal": "Provides candidate customer-notice facts; current dependencies and impact checks determine whether an update is needed",
    "agentWork.effect.finance": "Provides Finance candidate facts required by this task; financial rule changes take effect through the control plane after approval by the designated owner",
    "agentWork.effect.gtm": "Provides Quote or impact-analysis candidates; only the control plane can write the final Quote",
    "agentWork.effect.reviewer": "Blocks the incomplete first-round candidate, then permits composition only; grants neither human approval nor write authority",
    "topology.boundary.active": "ACTIVE BUSINESS RUN · {suggestion} · FROZEN RECEIPT · CONTROLLED LOCAL · NOT DISTRIBUTED PRODUCTION",
    "topology.boundary.activeOac": "OAC-BOUND TASK AND LEAST-PRIVILEGE CONTEXT · {suggestion} · CONTROLLED LOCAL · NOT DISTRIBUTED PRODUCTION",
    "modelSuggestion.vertexLive": "Captured real Vertex model advisory",
    "modelSuggestion.local": "Local model advisory",
    "modelSuggestion.receipt": "Model advisory receipt",
    "topology.boundary.fallback": "ACTIVE BUSINESS RUN · {mode} · AGENTTEAMS {agentteams} · CANDIDATE RUNTIME {runtime}",
    "topology.stage.oac": "01 · OAC TASK FORMATION",
    "topology.stage.manager": "02 · DETERMINISTIC COORDINATION",
    "topology.stage.domains": "03 · DOMAIN A1",
    "topology.stage.recovery": "04 · REVIEW, TOOL RECOVERY & REPLAN",
    "topology.stage.skill": "05 · AGENT CANDIDATE COMPOSITION & CONTROL ACCEPTANCE",
    "topology.stage.human": "06 · HUMAN",
    "topology.stage.canonical": "07 · CANONICAL",
    "topology.role.oacFormation": "OAC organization task formation",
    "topology.name.oacFormation": "Approved organization contract → minimum task team",
    "topology.status.oacFormation": "{domains} domains selected · actual AgentTeams topology matches",
    "topology.detail.upstream": "Upstream dependencies",
    "topology.detail.upstreamCount": "{count} accepted upstream tasks",
    "topology.detail.noUpstream": "No upstream business task",
    "topology.detail.input": "Input scope",
    "topology.detail.inputCount": "{count} governed inputs bound",
    "topology.detail.output": "Output nature",
    "topology.detail.candidateOutput": "Candidate result · not automatically effective",
    "topology.detail.canonicalOutput": "Approved internal canonical state",
    "topology.detail.controlOutput": "Acceptance or approval record · no direct business-state write",
    "topology.detail.authority": "Authority boundary",
    "topology.detail.candidateAuthority": "Produces candidates only; cannot write canonical state",
    "topology.detail.canonicalAuthority": "Only the OrgRebase control plane may write approved state",
    "topology.detail.controlAuthority": "Participates in acceptance or authorization only; cannot directly rewrite canonical business state",
    "topology.detail.execution": "Execution mode",
    "topology.detail.task": "Task in this step",
    "topology.detail.context": "Minimum context",
    "topology.detail.handoff": "Handoff to",
    "topology.detail.outputSummary": "Result produced",
    "topology.detail.reviewDecision": "Review decision",
    "topology.detail.tool": "Tool invoked by Agent",
    "topology.detail.skill": "Skill invoked by Agent",
    "topology.detail.effect": "Business effect",
    "topology.detail.oacCompiler": "Deterministic task-formation and least-context compiler",
    "topology.detail.logicalEventTime": "Validated against this controlled business event time",
    "topology.detail.agentExecution": "Agent produces a candidate; a later control gate owns acceptance",
    "topology.detail.toolExecution": "Governed Tool call returns a fact for later acceptance",
    "topology.detail.skillExecution": "Versioned Skill execution behind a release gate",
    "topology.detail.invocation": "AGENT INVOCATION IN THIS STEP",
    "topology.detail.invocationReceipt": "INVOCATION RECEIPT",
    "topology.detail.technicalAudit": "VIEW TECHNICAL AUDIT IDENTIFIERS",
    "topology.detail.humanExecution": "Owner reads the impact summary and explicitly clicks approve",
    "topology.detail.storeExecution": "Transactional write followed by event-chain sealing",
    "topology.detail.controlExecution": "Deterministic verification and control-plane decision",
    "topology.role.manager": "Control-plane task coordinator",
    "topology.name.managerRole": "Control-plane task coordinator",
    "topology.status.managerRole": "Planned · NOT AN AGENTTEAMS NATIVE TASK · control-plane role",
    "topology.detail.managerBoundary": "DETERMINISTIC HOST COORDINATION · NOT PRESENTED AS A NATIVE MANAGER TASK",
    "topology.status.atReceived": "AGENTTEAMS NATIVE RECEIPT COMPLETE · BUSINESS ADMISSION IS DECIDED BY THE LATER CONTROL GATE",
    "topology.status.atReceivedAbstain": "AGENTTEAMS RECEIVED · ABSTAIN · REPLAN REQUIRED",
    "topology.status.atReceivedRecovered": "AGENTTEAMS RECEIVED · MISSING FACT RECOVERED",
    "topology.status.toolRecovered": "CALLED DEPENDENCY-READ TOOL · FACT RECOVERED",
    "topology.status.skillComposed": "CALLED ENTERPRISE QUOTE COMPOSE SKILL · CANDIDATE FORMED",
    "topology.status.atReceivedDecision": "AGENTTEAMS RECEIVED · REVIEW DECISION {decision}",
    "topology.role.workerA1": "WORKER · {domain} · A1",
    "topology.role.workerA2": "WORKER · FINANCE · A2",
    "topology.role.gtmCompose": "GTM AGENT · CANDIDATE COMPOSITION",
    "topology.role.reviewerA1": "REVIEWER · PHASE 1",
    "topology.role.reviewerA2": "REVIEWER · PHASE 2",
    "topology.name.quoteComposeSkill": "ENTERPRISE QUOTE COMPOSE SKILL",
    "topology.role.control": "DETERMINISTIC CONTROL GATE",
    "topology.role.human": "HUMAN OWNER · SERVER-GATED",
    "topology.status.humanApprovalBound": "ROUND {round} · {change} APPROVAL BOUND",
    "topology.role.canonical": "CANONICAL STATE OWNER",
    "topology.summary.oac.task": "Read the approved organizational contract and employee request, then form the team required for this task",
    "topology.summary.oac.context": "Organization snapshot, task demand, role authorities, and minimum field projections",
    "topology.summary.oac.output": "A fixed team, dependency plan, and context envelopes for this run",
    "topology.summary.oac.effect": "Teams may vary across tasks; the plan is frozen within one run and changes require a new revision",
    "topology.summary.manager.task": "Split the approved plan into four domain tasks and a review barrier",
    "topology.summary.manager.context": "Only the OAC-formed execution plan and four domain projection summaries",
    "topology.summary.manager.output": "AgentTeams task dependency graph and controlled bindings",
    "topology.summary.manager.handoff": "Product, Legal, Finance, and GTM domain Agents",
    "topology.summary.manager.effect": "Coordinates tasks only; it produces no business conclusion and cannot write canonical state",
    "topology.summary.domain.task": "The {domain} Agent handles only its domain slots and produces a candidate",
    "topology.summary.domain.context": "Minimum {domain} context · {count} controlled inputs",
    "topology.summary.domain.output": "Completed {domain} domain candidate",
    "topology.summary.domain.abstain": "Abstained: a complete price-band fact was not observed",
    "topology.summary.domain.effect": "Produces a domain candidate only; it cannot take effect before review and the control gate",
    "topology.summary.review1.task": "Aggregate the four domain candidates and check slot completeness",
    "topology.summary.review1.context": "First-round Product, Legal, Finance, and GTM candidates",
    "topology.summary.review1.output": "A replan request returned to coordination",
    "topology.summary.review1.decision": "The Finance candidate lacked the price-band field, so the reviewer refused to guess and requested evidence",
    "topology.summary.review1.effect": "The candidate was not admitted; canonical state remained unchanged",
    "topology.summary.finance2.task": "Recover the Finance price-band fact requested by the replan",
    "topology.summary.finance2.context": "The first-round gap plus read-only dependency evidence",
    "topology.summary.finance2.output": "A repaired Finance domain candidate",
    "topology.summary.finance2.effect": "The Tool only recovered the missing fact; canonical writes remained zero",
    "topology.summary.review2.task": "Recheck the four domain candidates after evidence recovery",
    "topology.summary.review2.context": "Three accepted domain candidates and the second Finance candidate",
    "topology.summary.review2.output": "Candidate result accepted by review",
    "topology.summary.review2.decision": "All four domain slots, sources, and scopes passed deterministic verification",
    "topology.summary.review2.effect": "Allows the candidate to enter composition; canonical state is still unchanged",
    "topology.summary.gtm.task": "After review passes, the GTM Agent composes the four domain results",
    "topology.summary.gtm.context": "Review pass, four domain candidates, and the Tool receipt",
    "topology.summary.gtm.output": "An enterprise quote candidate awaiting control-gate acceptance",
    "topology.summary.gtm.effect": "The Skill is invoked by the Agent, not a standalone participant; target writes remain zero",
    "topology.summary.control.task": "Verify the run, digests, Skill release state, and candidate authority boundary",
    "topology.summary.control.context": "AgentTeams, Tool, and Skill receipts bound to the same run ID",
    "topology.summary.control.output": "Atomically form the enterprise quote collaborative draft",
    "topology.summary.control.handoff": "Human change-approval phase",
    "topology.summary.control.effect": "Formation is committed by a control-plane transaction; Agent, Tool, and Skill still have no canonical write authority",
    "topology.summary.human.task": "Review the impact summary for {change} and explicitly click approve",
    "topology.summary.human.context": "Zero-write preview, affected scope, insufficient-evidence items, and owner identity",
    "topology.summary.human.output": "A human approval receipt bound to this exact preview",
    "topology.summary.human.handoff": "OrgRebase canonical-state control plane",
    "topology.summary.human.effect": "Authorizes only the exact change; it does not mean customer acceptance or external publication",
    "topology.summary.store.task": "Consume the approved change and perform a selective Rebase",
    "topology.summary.store.context": "Approval receipt, formation receipt, and current canonical revision",
    "topology.summary.store.output": "Committed {quote}",
    "topology.summary.store.handoff": "Task acceptance result and audit record",
    "topology.summary.store.effect": "Rebuilds only affected items, preserves provably unaffected items, and sends insufficient evidence to people",
    "acceptance.story.currentComplete": "Verified {count} change receipts and the current result for this run. Items held for human review remain open.",
    "acceptance.story.currentPending": "Review the current result and individual decisions in the workbench. Complete receipts are not yet verified; pending or unknown states are not complete.",
    "acceptance.story.complete": "This task is accepted: two human approvals, two selective Rebase receipts, and the final quote are bound to the same run.",
    "acceptance.story.waiting": "This task has not reached a verifiable terminal state; the page does not precompute savings or fabricate results.",
    "acceptance.story.observed": "Observed",
    "acceptance.story.pending": "Awaiting completion",
    "acceptance.story.step.employee": "Employee confirms the work request",
    "acceptance.story.step.employeeDetail": "The employee confirms task scope only; this does not form the quote or grant business approval.",
    "acceptance.story.step.auto": "System forms and coordinates the team",
    "acceptance.story.step.autoDetail": "OAC forms the minimum team; domain candidates are automatically admitted only after contract, source, scope, permission, and completeness checks, and insufficient evidence is escalated.",
    "acceptance.story.step.product": "Product owner approves the launch date",
    "acceptance.story.step.productDetail": "After reading the zero-write preview, the exact owner clicks only after the server review-time gate.",
    "acceptance.story.step.finance": "Finance owner approves the quote currency",
    "acceptance.story.step.financeDetail": "Only this currency change is approved; stale approval or an owner mismatch is rejected.",
    "acceptance.story.step.apply": "Control plane performs selective writes",
    "acceptance.story.step.applyDetail": "Only approved fields are applied, provably unaffected items are preserved, and the final result becomes downloadable.",
    "acceptance.story.candidateBoundary": "Candidate handling boundary",
    "acceptance.story.candidateEmployee": "Employee task: employee confirms scope",
    "acceptance.story.candidateDomain": "Domain candidate: auto-admit after checks; escalate only when insufficient",
    "acceptance.story.candidateCanonical": "Canonical change: exact domain owner approves",
    "acceptance.story.candidateSkill": "Skill experience candidate: independent evaluation plus capability-owner approval",
    "acceptance.story.change.launch": "Product launch date",
    "acceptance.story.change.currency": "Quote currency",
    "acceptance.story.change.delta": "{from} → {to}",
    "acceptance.story.change.owner": "Approved by: {owner}",
    "acceptance.story.change.wait": "Review-time gate: satisfied",
    "acceptance.story.change.metrics": "Rebuilt {rebuilt} · preserved {preserved} · human review {unknown}",
    "acceptance.story.change.safety": "False invalidations {falseInvalidations} · unauthorized disclosures {unauthorized}",
    "acceptance.story.change.waiting": "This change has not produced a complete receipt.",
    "acceptance.story.final": "Final accepted result",
    "acceptance.story.finalSummary": "{quote} · launch {launch} · currency {currency}",
    "acceptance.story.finalWaiting": "Waiting for two approvals, Rebase receipts, and the final quote to pass same-run verification.",
    "value.process.deliveryBoundary": "Enterprise target process · the current system does not manage customer publication or customer acceptance",
    "lifecycle.kicker": "QUOTE V1 BASELINE FORMATION REPLAY · NOT A CURRENT CHANGESET RERUN",
    "lifecycle.title": "BASELINE AGENTTEAMS NATIVE TASK LIFECYCLE",
    "lifecycle.waiting": "WAITING FOR CURRENT RUN",
    "lifecycle.run": "SAME RUN · {actions} NATIVE ACTIONS · CONTROLLED LOCAL · NOT DISTRIBUTED PRODUCTION",
    "lifecycle.authorityBoundary": "AT received ≠ control admitted ≠ human approved ≠ canonical applied",
    "lifecycle.boundary": "This rail shows native task facts from the current frozen business run only. Conflict reassignment, process recovery, and compensation remain separate validation runs and are not merged into this rail.",
    "lifecycle.step.project": "PROJECT / DAG TASK CREATION",
    "lifecycle.step.project.detail": "{revisions} PLAN REVISIONS · {tasks} NATIVE TASKS",
    "lifecycle.step.delegate": "DELEGATE",
    "lifecycle.step.delegate.detail": "{count}/{total} COMPLETED BINDINGS",
    "lifecycle.step.ack": "ACK",
    "lifecycle.step.ack.detail": "{count}/{total} BINDINGS EXECUTED AFTER ACK",
    "lifecycle.step.handoff": "CONTEXT / RESULT HANDOFF",
    "lifecycle.step.handoff.detail": "{context}/{total} CONTEXT BINDINGS · {result}/{total} RESULT HANDOFFS",
    "lifecycle.step.submitCheck": "SUBMIT / CHECK",
    "lifecycle.step.submitCheck.detail": "{count}/{total} NATIVE BINDINGS ROUND-TRIP CHECKED",
    "lifecycle.step.received": "AT RECEIVED",
    "lifecycle.step.received.detail": "{count}/{total} accept_task_result · NOT CONTROL ADMISSION",
    "lifecycle.step.terminal": "PROJECT TERMINAL",
    "lifecycle.step.terminal.detail": "{status} · {actions} NATIVE ACTIONS TOTAL",
    "lifecycle.state.observed": "OBSERVED",
    "lifecycle.state.partial": "PARTIAL",
    "lifecycle.state.waiting": "NOT OBSERVED",
    "lifecycle.stage.create": "CREATE",
    "lifecycle.stage.delegate": "DELEGATE",
    "lifecycle.stage.ack": "ACK",
    "lifecycle.stage.handoff": "CONTEXT HANDOFF",
    "lifecycle.stage.submit": "SUBMIT",
    "lifecycle.stage.check": "CHECK",
    "lifecycle.stage.accept": "ACCEPT",
    "lifecycle.stage.complete": "COMPLETE",
    "lifecycle.action.createProject": "create_project",
    "lifecycle.action.delegateTask": "delegate_task",
    "lifecycle.action.ackTask": "ack_task",
    "lifecycle.action.bindContext": "context_projection_digest",
    "lifecycle.action.submitTask": "submit_task",
    "lifecycle.action.checkTask": "check_task",
    "lifecycle.action.acceptTask": "accept_task_result",
    "lifecycle.action.completeProject": "complete_project",
    "collaboration.terminal.title": "Selective changes from collaborative working draft → currency confirmed draft",
    "experience.kicker": "POST-TERMINAL GOVERNANCE EXTENSION",
    "experience.title": "Experience Candidate → Governed Skill",
    "experience.explanation": "The system distills a candidate only from the observed replanning and evidence-recovery trace. A candidate never rewrites a Skill automatically; a Skill Steward must review it.",
    "experience.source": "SOURCE",
    "experience.evaluation": "EVALUATION",
    "experience.evaluation.detail": "eight partitions · target writes=0",
    "experience.authority": "AUTHORITY BOUNDARY",
    "experience.authority.detail": "not discoverable, loadable, or callable before approval",
    "experience.release": "RELEASE RESULT",
    "experience.steward": "HUMAN OWNER",
    "experience.waitingTerminal": "Waiting for the internal-change-loop terminal",
    "experience.reject": "Reject candidate",
    "experience.approve": "Approve controlled CANARY dry-run",
    "experience.boundary": "SINGLE_RUN_SEED · not consumed by this Quote · approval enables only a controlled-local RELEASE dry-call, not a production-generalization claim.",
    "experience.status.awaiting": "AWAITING HUMAN APPROVAL",
    "experience.status.approved": "APPROVED · CONTROLLED CANARY DRY-RUN CALLABLE",
    "experience.status.rejected": "CANDIDATE REJECTED",
    "experience.status.none": "NO CANDIDATE FOR THIS RUN",
    "experience.review.wait": "Server review gate · {seconds}s remaining",
    "experience.review.ready": "Review duration satisfied · Skill Steward decision required",
    "experience.review.approved": "Human approval binds the candidate, evaluation, and predecessor head",
    "experience.review.rejected": "Human rejected · not discoverable or callable",
    "experience.review.none": "Evidence was insufficient for a governed candidate",
    "experience.release.pending": "NOT RELEASED · candidate-only",
    "experience.release.rejected": "REJECTED · no release head",
    "experience.release.canary": "RELEASE dry-call PASS · not consumed by this Quote",
    "experience.toast.approved": "The Skill Steward approved the experience candidate for a controlled CANARY dry-run.",
    "experience.toast.rejected": "The experience candidate was rejected and did not enter the Skill release chain.",
    "experience.toast.wait": "Review the experience candidate first ({seconds}s).",
    "experience.button.review": "Review candidate · {seconds}s",
    "experience.pattern": "Finance missing price_band → ABSTAIN → REPLAN → Tool → PASS",
    "capability.current.quoteCompose": "Compose four domain results into an Enterprise Quote candidate for acceptance",
    "capability.current.success": "CANDIDATE FORMED · CANONICAL WRITES 0",
    "capability.current.receipt": "SAME-RUN RECEIPT BOUND",
    "capability.current.package": "ENTERPRISE QUOTE COMPOSE SKILL · {version}",
    "capability.current.waiting": "Awaiting a Skill invocation receipt from the current task",
    "skill.source.kicker": "PUBLISHED SKILL REVIEW",
    "skill.source.boundary": "Published source is read-only and cannot be overwritten. Changes are saved as unevaluated candidate drafts and do not affect the current Agent runtime.",
    "skill.source.close": "Close Skill source review",
    "skill.source.version": "PUBLISHED VERSION",
    "skill.source.state": "CURRENT STATE",
    "skill.source.draftCount": "SAVED CANDIDATES",
    "skill.source.proposedVersion": "CANDIDATE VERSION",
    "skill.source.versionHint": "The new version must exactly match the version field in the source front matter.",
    "skill.source.publishedText": "PUBLISHED SOURCE (READ ONLY)",
    "skill.source.draftText": "CANDIDATE DRAFT SOURCE (EDITABLE)",
    "skill.source.technical": "TECHNICAL VERIFICATION",
    "skill.source.packageDigest": "PACKAGE DIGEST",
    "skill.source.textDigest": "SOURCE DIGEST",
    "skill.source.savedDrafts": "SAVED CANDIDATE DRAFTS",
    "skill.source.draftsBoundary": "Draft only; not evaluated, not released, and not executable",
    "skill.source.cancel": "Cancel revision",
    "skill.source.revise": "Create revision from this version",
    "skill.source.save": "Save candidate draft",
    "skill.source.catalogTitle": "SOURCE REVIEW AND REVISION",
    "skill.source.catalogBoundary": "Each viewer opens the exact source from the published package. Every change creates a separate candidate.",
    "skill.source.catalogLoading": "Verifying and loading published Skill source…",
    "skill.source.catalogUnavailable": "Published Skill source could not be verified. This page will not fabricate released content.",
    "skill.display.enterpriseQuoteCompose": "Enterprise quote composition Skill",
    "skill.display.structuredDomainHandoff": "Structured domain handoff Skill",
    "skill.display.enterpriseLaunchReadiness": "Enterprise launch readiness Skill",
    "skill.display.stableId": "STABLE ID: {id}",
    "skill.source.bytes": "{count} VERIFIED BYTES",
    "skill.source.draftCountValue": "{count} CANDIDATE DRAFTS",
    "skill.source.open": "View source and revise",
    "skill.source.publishedImmutable": "PUBLISHED · SOURCE IMMUTABLE",
    "skill.source.readonlyStatus": "Viewing the exact published source. Create an independent candidate draft to propose a change.",
    "skill.source.editingStatus": "Editing a candidate draft. Saving neither rewrites the published version nor makes the Agent use it immediately.",
    "skill.source.savedStatus": "Candidate draft {version} persisted; not evaluated, not released, and not executable.",
    "skill.source.errorStatus": "Candidate draft was not saved: {message}",
    "skill.source.noDrafts": "No candidate drafts saved.",
    "skill.source.draftReceipt": "CANDIDATE {version} · NOT EVALUATED · NOT RELEASED · NOT EXECUTABLE",
    "skill.source.draftOwner": "REVISOR: SKILL STEWARD",
    "skill.source.invalidVersion": "Enter a valid semantic version.",
    "formationProof.title": "DYNAMIC FORMATION PLAN RECONCILIATION",
    "formationProof.kicker": "ON-DEMAND TEAM PROOF · SEPARATE RUN",
    "formationProof.loading": "Reading Formation run…",
    "formationProof.match": "Formation planned {planned} domains = AgentTeams ran {actual} domains · {reviewers} reviewer barrier · {actions} native actions",
    "formationProof.boundary": "on-demand {domains} · candidate-only · 0 canonical writes · independent pinned in-process replay",
    "formationProof.unavailable": "Dynamic formation evidence unavailable · fail closed · planned and actual topology hidden",
    "formationProof.planRole": "DETERMINISTIC FORMATION COMPILER",
    "formationProof.planName": "MINIMUM DOMAIN PLAN",
    "formationProof.planStatus": "planned {planned} = actual {actual}",
    "formationProof.workerRole": "DOMAIN AGENT · {domain}",
    "formationProof.reviewerRole": "INDEPENDENT REVIEWER BARRIER",
    "formationProof.reviewerName": "Independent Reviewer",
    "formationProof.summary": "planned {planned} domains = actual {actual} domains · {actions} native actions",
    "formationProof.runBoundary": "{runId} · candidate-only · canonical writes {writes}",
    "retained.description": "A separate frozen run first reconciles a two-domain formation plan with the actual AgentTeams topology, then independently validates authority boundaries, conflict resolution, reassignment, and late-result fencing. It is not a second execution of the current Quote task.",
    "mechanism.strategy.orgrebase-workspace": "Full reference implementation",
    "mechanism.strategy.safe-single-agent-admitted-context": "Safe single agent (admitted context)",
    "mechanism.strategy.unified-raw-context-stress": "Unified raw-context stress control",
    "mechanism.strategy.natural-language-multi-agent": "Natural-language multi-agent",
    "mechanism.strategy.broadcast-invalidate-all": "Invalidate all by broadcast",
    "mechanism.strategy.no-reference-monitor": "Without reference monitoring",
    "mechanism.strategy.no-context-projection": "Without context projection",
    "mechanism.strategy.no-manifest-bijection": "Without manifest bijection",
    "mechanism.strategy.no-certificate": "Without certificates",
    "mechanism.strategy.no-successor-promotion": "Without successor promotion",
    "mechanism.strategy.no-unknown": "Without Unknown handling",
    "mechanism.strategy.no-skill-requalification": "Without Skill requalification",
    "mechanism.strategy.non-exact-skill-digest-negative-control": "Non-exact Skill digest negative control",
    "mechanism.title": "Reference mechanism comparison · Synthetic cases",
    "mechanism.boundary": "OWB v1.1 · Parallel ReferenceWorkspaceBenchmarkSUT · 192 synthetic cases per strategy. Not a packaged-product or live-LLM comparison, current task, customer result, or ROI.",
    "mechanism.caution": "100 is the maximum controlled reference benchmark value, not a measure of realized business benefit. A score of 90 still requires every hard gate to pass. Some ablations pass; this does not establish a benefit for every mechanism in every scenario. Fixed completed_at values are not execution timestamps.",
    "mechanism.unavailable": "Reference archive missing or invalid. Results cleared; current quotes are unaffected.",
    "mechanism.ready": "13 validated strategies · the same 192 cases · read-only historical results",
    "mechanism.profile": "Strategy",
    "mechanism.score": "Benchmark / 100",
    "mechanism.gates": "Hard gates",
    "mechanism.result": "Overall",
    "mechanism.reference": "Reference",
    "mechanism.baselines": "Baseline",
    "mechanism.ablations": "Ablation",
    "mechanism.source": "Source SHA-256",
    "mechanism.path": "Path in source package",
    "mechanism.quoteDigest": "Binding quote-value receipt digest",
    "mechanism.registryDigest": "Benchmark registry digest",
    "mechanism.threshold": "Pass threshold: 90 + all hard gates",
    "retained.archive.title": "Formation, permissions, and result isolation",
    "retained.archive.summary": "Local run records · Separate from the current task",
    "retained.quoteLoop.kicker": "CONTROL-SEMANTICS AUDIT",
    "retained.quoteLoop.title": "AUTHORITY, CONFLICT & RESULT FENCING",
    "retained.quoteLoop.boundary": "SEPARATE CONTROLLED RUN · PROVES CONTROL SEMANTICS ONLY · DOES NOT REPEAT THE ACTIVE BUSINESS FLOW",
    "retained.authority.detail": "AgentTeams produces scheduling and candidate facts only",
    "retained.boundary.detail": "The retained successor is not merged with the active pilot",
    "retained.successor.detail": "scripted local approval · canonical-state control plane only",
    "retained.unavailable": "Mechanism-validation record unavailable · fail closed",
    "retained.empty": "Mechanism-validation record unavailable; nothing was inferred or fabricated.",
    "operations.events.waiting": "Waiting for events",
    "operations.events.scoped": "quote-business chain {quoteStatus} · {quote} events; workspace global chain {global}, including {oac} OAC-governance events",
    "operations.persistence.detail": "Persistence and recovery state",
    "operations.sameRun.kicker": "ACTIVE BUSINESS-RUN EVIDENCE",
    "operations.sameRun.verified": "The active run produced 5/5 receipt stages for the Quote-formation subchain; this does not mean both human approvals and selective Rebase reached terminal state",
    "operations.sameRun.verifiedComplete": "The active business run is complete: the 5/5 Quote-formation receipt stages, both human approvals, and both selective Rebase operations have been verified",
    "operations.sameRun.partial": "The active run produced {observed}/{total} receipt stages for the Quote-formation subchain",
    "operations.sameRun.unavailable": "The active task has not produced a business-run receipt",
    "operations.sameRun.projectionVerified": "Active-run terminal projection verified · seven-layer causal structure complete",
    "operations.sameRun.projectionWaitingArchive": "The task is terminal · waiting for the active-run archive before requesting the observability projection",
    "operations.sameRun.projectionLoading": "The active-run archive passed · requesting the terminal observability projection",
    "operations.sameRun.projectionClosed": "Terminal observability projection closed · retaining the five-stage Quote-formation evidence",
    "operations.sameRun.boundary": "This area proves only the active run's Quote-formation subchain: every stage must match both the active run ID and a receipt digest. Human approvals and business terminal state require separate proof from this page's task-acceptance cards; OTLP, alert, capacity, and recovery evidence lives under Runtime Assurance.",
    "operations.sameRun.boundaryComplete": "The five receipt stages here prove the active run's Quote-formation subchain. The task-acceptance cards above separately verify both human approvals, selective Rebase operations, and the terminal receipt for that same run. OTLP, alert, capacity, and recovery remain independent engineering validations under Runtime Assurance and are not borrowed by the active run.",
    "operations.sameRun.projectionBoundary": "The browser only cross-checks the active run's seven-layer causal structure, correlation fields, and zero-write declaration. The server seals the OTLP summary; this page does not recompute its digest. This remains a controlled-local terminal projection, not realtime observation, a production backend, or SLA evidence.",
    "operations.sameRun.projectionClosedBoundary": "The active archive or projection did not pass same-run cross-checks, so the terminal observability projection remains closed. The five-stage Quote-formation receipts stay visible, frozen evidence is never borrowed, and the separately validated business archive is unaffected.",
    "operations.sameRun.currentLane": "ACTIVE RUN · QUOTE-FORMATION SUBCHAIN · 5 STAGES",
    "operations.sameRun.projectionLane": "ACTIVE RUN · TERMINAL CAUSAL PROJECTION · 7 LAYERS",
    "operations.sameRun.projectedNodeDetail": "structure cross-checked · controlled-local · 0 external target writes · server-sealed summary bound",
    "operations.sameRun.candidateLane": "CANDIDATE CAUSAL TRACE · 5 SPANS",
    "operations.sameRun.observabilityLane": "OTLP OBSERVABILITY CARRIER",
    "operations.sameRun.governanceLane": "GOVERNED SUCCESSOR · LINKED EXECUTION-CHAIN PACK",
    "operations.retained.title": "Historical operations verification",
    "operations.retained.boundary": "Separate run records",
    "operations.delivery.title": "Independent Engineering Delivery Validation",
    "operations.otlp.detail": "traces / logs / metrics are queryable",
    "operations.readiness.detail": "local artifacts verified; production SLA / HA / geo-DR / supply-chain signing not run",
    "operations.audit.title": "Runtime Assurance Details",
    "operations.audit.summary": "Expand observability, connector, retention, and production-acceptance boundaries",
    "operations.audit.unavailable": "No validated operations summary is available; this view does not infer or fabricate state.",
    "operations.audit.run.title": "Correlation inside the independent validation run",
    "operations.audit.runId": "Exact run ID",
    "operations.audit.chain": "Candidate causal chain",
    "operations.audit.chainPass": "Five-span causal trace correlated; governed write remains a separate successor pack",
    "operations.audit.otlp.title": "OTLP observability contract",
    "operations.audit.signals": "Exported and queryable signals",
    "operations.audit.fields": "Correlation fields ({count})",
    "operations.audit.signal.traces": "Traces",
    "operations.audit.signal.logs": "Logs",
    "operations.audit.signal.metrics": "Metrics",
    "operations.audit.field.deploymentEnvironment": "Deployment environment",
    "operations.audit.field.serviceName": "Service name",
    "operations.audit.field.serviceVersion": "Service version",
    "operations.audit.field.runId": "Run ID",
    "operations.audit.field.taskId": "AgentTeams task ID",
    "operations.audit.field.skillName": "Skill name",
    "operations.audit.field.toolReceipt": "Tool receipt digest",
    "operations.audit.field.receipt": "Runtime receipt digest",
    "operations.audit.field.targetWrites": "Target write count",
    "operations.audit.connectors.title": "Connector status",
    "operations.audit.connector.source": "Local Source HTTP",
    "operations.audit.connector.tool": "Local Tool HTTP",
    "operations.audit.connector.crm": "Customer Relationship Management (CRM)",
    "operations.audit.connector.cpq": "Configure, Price, Quote (CPQ)",
    "operations.audit.connector.clm": "Contract Lifecycle Management (CLM)",
    "operations.audit.connector.erp": "Enterprise Resource Planning (ERP)",
    "operations.audit.connector.knowledgeBase": "Enterprise knowledge base",
    "operations.audit.connector.pendingEnterprise": "Pending enterprise integration",
    "operations.audit.retention.title": "Retention and deletion boundary",
    "operations.audit.indexedTelemetry": "Indexed telemetry retention",
    "operations.audit.rejectedDiagnostics": "Rejected-payload diagnostic retention",
    "operations.audit.businessEvidence": "Canonical business evidence",
    "operations.audit.businessEvidenceUnaffected": "Not deleted by telemetry retention",
    "operations.audit.rejectedRaw": "Rejected raw payload",
    "operations.audit.rawNotPersisted": "Not persisted",
    "operations.audit.retentionPeriod": "{days} days ({seconds} seconds)",
    "operations.audit.readiness.title": "Deployment and production boundary",
    "operations.audit.deployment": "Deployment profile",
    "operations.audit.productionReady": "Production ready",
    "operations.audit.productionPilot": "No · controlled pilot MVP",
    "operations.audit.contractualSla": "Contractual SLA",
    "operations.audit.geoDr": "Geographic disaster-recovery failover",
    "operations.audit.sbom": "Software Bill of Materials (SBOM)",
    "operations.audit.contributing": "Contribution guide",
    "operations.audit.localBoundary": "This view exposes only validated controlled-local summaries. It does not expand raw payloads or mark unrun enterprise or production capabilities as complete.",
    "operations.evidenceIndex.title": "Evidence Index · Frozen Independent Validation Archive",
    "operations.evidenceIndex.summary": "Expand the verified index, evidence classes, and privacy-rejection result",
    "operations.evidenceIndex.facts": "MINIMUM FROZEN-INDEX PROJECTION",
    "operations.evidenceIndex.runId": "Archive run ID",
    "operations.evidenceIndex.entryCount": "Indexed entries",
    "operations.evidenceIndex.indexDigest": "Index digest",
    "operations.evidenceIndex.packDigest": "Evidence-pack digest",
    "operations.evidenceIndex.classes": "Evidence classes",
    "operations.evidenceIndex.verifier": "Index verifier",
    "operations.evidenceIndex.privacy": "Privacy canary / rejection",
    "operations.evidenceIndex.targetWrites": "Read-model target writes",
    "operations.evidenceIndex.boundary": "SEPARATE FROZEN RUN · NOT THE ACTIVE QUOTE RUN · NO BORROWED PROOF. Only digests, evidence classes, and rejection reasons are projected; raw or sensitive values remain hidden.",
    "operations.evidenceIndex.verified": "INDEX CLOSURE, FILE DIGESTS, AND PRIVACY REJECTION VERIFIED",
    "operations.evidenceIndex.unavailable": "The frozen Evidence Index is unavailable; this view does not expose partial results.",
    "operations.evidenceIndex.entryCountValue": "{count} ENTRIES · FULL FILE BINDING",
    "operations.evidenceIndex.privacyValue": "{status} · {canary} / {rejection} · RAW PAYLOAD NOT RETAINED",
    "operations.evidenceIndex.targetWritesValue": "{count} (READ-ONLY PROJECTION)",
    "operations.failures.title": "FAILURE HANDLING RULES",
    "operations.failures.boundary": "static handling rules · runtime receipts are shown in this page's independent validation archive",
    "operations.failure.403.title": "Owner mismatch",
    "operations.failure.403.detail": "approval rejected · target writes=0",
    "operations.failure.409.title": "Stale / conflicting input",
    "operations.failure.409.detail": "Apply rejected · current canonical state retained",
    "operations.failure.block.title": "Pack / DB mismatch",
    "operations.failure.block.detail": "fail closed before startup · no automatic migration",
    "operations.failure.kill.title": "Independent process recovery",
    "operations.health.unavailable": "/api/health returned no payload",
    "operations.ready.unavailable": "No valid readiness status from /readyz",
    "operations.ready.confirmed": "Service readiness probe passed",
    "operations.ready.deployment": "Current deployment: {maturity}",
    "operations.ready.production.false": "Production readiness not claimed",
    "operations.ready.production.true": "Deployment reports production readiness; the probe does not replace production acceptance",
    "operations.ready.profile": "Profile {digest}",
    "operations.ready.pack": "Rule pack {digest}",
    "operations.ready.maturity.REFERENCE_RUNTIME": "Reference runtime",
    "operations.ready.maturity.SINGLE_ENTERPRISE_PILOT": "Single-enterprise pilot",
    "operations.ready.maturity.AUTHENTICATED_SINGLE_TENANT": "Authenticated single tenant",
    "operations.ready.notReady": "Not ready",
    "operations.ready.notReadyDetail": "The readiness probe reports that the service is not ready",
    "operations.workspaceStoreConnected": "workspace store connected",
    "operations.persistence.checking": "Checking the current workspace's storage connection.",
    "operations.persistence.ready": "The current workspace is readable and the storage readiness probe passed.",
    "operations.persistence.unavailable": "The current workspace's storage connection is unconfirmed; retained records do not prove present availability.",
    "operations.boundary.active": "The active Golden business run completed the local loop and reopened completed SQLite state, but did not demonstrate interrupted-run resume. A separate controlled-local operations run verified two SIGKILL recoveries: retry intent and adoption of committed work. Cross-process recovery for autonomous distributed AgentTeams remains not run. Real enterprise connectors and data, ROI, production SLA / HA / geo-DR, and multi-tenancy remain pending enterprise validation.",
    "operations.boundary.formation": "The active Golden run has verified only the Quote-formation subchain. Both human approvals, selective Rebase operations, and terminal receipts are not all complete, so this is not a complete business loop. OTLP, alert, capacity, and recovery below belong to a separate engineering-validation archive.",
    "operations.boundary.inactive": "Current coverage includes the controlled-local pilot and operations contracts; real connectors, enterprise ROI, production SLA / HA / geo-DR, and multi-tenancy still require enterprise-environment validation.",
    "quote.empty.title": "No internal quote working draft has been formed",
    "quote.empty.body": "OAC is active, but Quote v1 has not been formed yet. Starting the task first runs pinned baseline Formation; only later ChangeSets project a minimum change_team from durable receipts and reuse admitted capabilities.",
    "quote.field.customer": "CUSTOMER / QUOTE OWNER",
    "quote.field.plan": "ENTERPRISE PLAN",
    "quote.field.launch": "LAUNCH DATE",
    "quote.field.residency": "DATA RESIDENCY",
    "quote.field.notice": "CUSTOMER NOTICE",
    "quote.field.price": "PRICE BAND",
    "quote.field.currency": "CURRENCY",
    "quote.field.terms": "PARTNER TERMS",
    "quote.field.workflow": "INTERNAL WORK STATUS",
    "quote.field.decision": "ACTIVE CHANGE DECISION",
    "quote.field.externalLifecycle": "EXTERNAL COMMERCIAL LIFECYCLE",
    "quote.field.lineage": "REVISION LINEAGE",
    "quote.revision": "Governed working draft · technical update {number}",
    "quote.stage.collaborative": "Collaborative working draft",
    "quote.stage.launchConfirmed": "Launch-date confirmed draft",
    "quote.stage.currencyConfirmed": "Currency confirmed draft",
    "quote.workflow.revision1": "Collaborative working draft · awaiting product-date change",
    "quote.workflow.revision2": "Launch-date confirmed draft · awaiting finance-currency change",
    "quote.workflow.revision3": "Currency confirmed draft · internal change loop verified",
    "quote.lineage.summary": "{path} · governed selective Rebase",
    "quote.lineage.current": "Current {version}",
    "quote.external.unmanaged": "Not managed by this system · no customer publication or acceptance evidence observed",
    "quote.decision.none": "No human change decision pending",
    "quote.decision.launchPending": "Product-date change · awaiting Product Owner approval",
    "quote.decision.launchApproved": "Product-date change · approved, awaiting control-plane apply",
    "quote.decision.currencyPending": "Currency change · awaiting Finance Owner approval",
    "quote.decision.currencyApproved": "Currency change · approved, awaiting control-plane apply",
    "quote.coalition.title": "Minimum Domain Coalition",
    "quote.coalition.ready": "4 DOMAINS · COVERED",
    "quote.defaultLabel": "Enterprise Quote Conditions Pack",
    "quote.customerUnknown": "customer not declared",
    "quote.ownerUnknown": "owner not declared",
    "quote.notice.required": "Notice required",
    "quote.notice.notRequired": "No notice required",
    "domain.product": "Product",
    "domain.product.detail": "plan · date · residency",
    "domain.legal": "Legal",
    "domain.legal.detail": "notice obligations",
    "domain.finance": "Finance",
    "domain.finance.detail": "price band · currency",
    "domain.gtm": "GTM",
    "domain.gtm.detail": "quote assembly · partner terms",
    "impact.title": "Pre-approval Preview + VMRC Minimal Rebuild Certificate",
    "impact.vmrcBoundary": "VMRC binds the ChangeSet, Preview, and each object-level effect exactly. A missing rebuild, an extra rebuild, or treating UNKNOWN as preservable rejects Apply.",
    "impact.rebuild": "MUST REBUILD",
    "impact.preserve": "PROVEN SAFE TO KEEP",
    "impact.unknown": "INSUFFICIENT EVIDENCE",
    "impact.requalify": "SKILL REQUALIFICATION",
    "impact.empty": "After a change preview, each target's classification, proof path, and certificate appear here.",
    "impact.emptyLocked": "The preview is locked, but the response contains no target details.",
    "impact.noProofPath": "NO PROOF PATH DECLARED",
    "governance.title": "Approvals, Successor Graph, and Event Chain",
    "governance.approver": "APPROVER",
    "governance.noEvents": "No business events yet",
    "governance.events": "{count} QUOTE-BUSINESS EVENTS · INTEGRITY VERIFIED",
    "governance.passSeal": "OK",
    "governance.waitSeal": "···",
    "governance.zeroWrites": "0 TARGET WRITES",
    "footer.copy": "OrgRebase · Governed Enterprise Work Evolution · controlled demo scenario",
    "footer.api": "Developer API (OpenAPI)",
    "common.loading": "Loading…",
    "common.currentValue": "current value",
    "common.declaredVersion": "declared successor",
    "common.evidenceUnavailable": "Evidence unavailable · fail closed",
    "action.empty.label": "Generate internal working draft",
    "action.empty.title": "Form an evolvable Enterprise Quote baseline",
    "action.empty.detail": "Create a Quote first. When rules change, the system checks the impact, coordinates candidate updates, and requests approval from the designated owner.",
    "action.empty.role": "Quote operations owner",
    "action.oacGate.label": "Open OAC enterprise adaptation",
    "action.oacGate.title": "The organizational contract is not admitted; quote formation is paused",
    "action.oacGate.detail": "Complete the five enterprise-input mappings, deterministic OAC validation, and the four-second owner admission. This button then returns to Generate internal working draft.",
    "action.oacGate.actor": "OAC business start gate",
    "action.oacGate.role": "Deterministic precondition gate · does not replace human admission",
    "action.oacChecking.label": "Checking the OAC business start gate…",
    "action.oacChecking.title": "Reading the organizational-contract admission state",
    "action.oacChecking.detail": "The UI fails closed until validation completes; it does not report admitted or blocked before the authoritative state arrives.",
    "action.oacChecking.actor": "OAC business start gate",
    "action.oacChecking.role": "Reading the authoritative server state",
    "action.experienceReview.label": "Review experience candidate",
    "action.experienceReview.title": "An experience candidate awaits human governance; current receipts show business completion",
    "action.experienceReview.detail": "This only navigates to the candidate generated by this run. The candidate cannot publish itself or rewrite a Skill.",
    "action.experienceReview.actor": "Skill Steward",
    "action.experienceReview.role": "Candidate release decision owner · explicit human authority",
    "action.launchPreview.label": "Receive and preview product-date change",
    "action.launchPreview.title": "An upstream Product change arrived; compute its impact automatically",
    "action.launchPreview.detail": "With zero writes, compute what must rebuild, what is proven safe to keep, UNKNOWNs, and Skill requalification boundaries. No Owner approval is required for this step.",
    "action.launchPreview.role": "UPSTREAM CHANGE EVENT · AUTOMATIC IMPACT COMPUTATION",
    "action.launchApprove.label": "Product Owner approves this change",
    "action.launchApprove.title": "Explicitly approve the locked product-change preview",
    "action.launchApprove.detail": "Approval binds the current preview digest; the backend rejects digest drift.",
    "action.launchApprove.role": "Product Owner · local declared identity · exact binding",
    "action.launchApply.label": "Apply change · form launch-date confirmed draft",
    "action.launchApply.title": "Consume the approval receipt and selectively Rebase",
    "action.launchApply.detail": "The control plane may consume only the approved digest; it cannot self-approve or recompute the preview.",
    "action.control.role": "Deterministic control plane · canonical writer",
    "action.owner.role": "Designated owner · approval of the exact proposal",
    "action.currencyPreview.label": "Receive and preview finance-currency change",
    "action.currencyPreview.title": "An upstream Finance-policy change arrived; compute its impact automatically",
    "action.currencyPreview.detail": "The launch-date confirmed draft is a durable checkpoint; the system continues with a zero-write Preview. No Owner approval is required for this step.",
    "action.currencyPreview.role": "UPSTREAM CHANGE EVENT · AUTOMATIC IMPACT COMPUTATION",
    "action.currencyApprove.label": "Finance Owner approves this change",
    "action.currencyApprove.title": "Explicitly approve the locked currency-change preview",
    "action.currencyApprove.detail": "Only the Finance Owner may issue approval bound to this preview digest.",
    "action.currencyApprove.role": "Finance Owner · local declared identity · exact binding",
    "action.currencyApply.label": "Apply change · form currency confirmed draft",
    "action.currencyApply.title": "Complete the second selective Rebase",
    "action.currencyApply.detail": "Produce successor work, the successor dependency graph, receipts, and a verifiable event chain.",
    "action.complete.label": "Internal selective-Rebase loop verified",
    "action.complete.title": "Currency confirmed draft · change loop verified",
    "action.complete.summary": "{revision} · {launch} · {currency}",
    "action.complete.detail": "Pack assembly, two local Owner commands, the successor graph, and the event chain are all downloadable for review.",
    "action.complete.role": "Read-only current quote · change and review records are available in the linked task",
    "action.dynamic.start": "Start {label}",
    "action.delta.launch": "Product launch date · {base} → {proposed}",
    "action.delta.currency": "Quote currency · {base} → {proposed}",
    "currency.usd": "US dollars (USD)",
    "currency.eur": "Euros (EUR)",
    "action.complete.goldenRole": "Read-only run view · REVIEWER PASS · CANONICAL PERSISTED",
    "action.complete.goldenDetail": "AgentTeams, Tool, Skill, human approvals, selective Rebase, and the event chain are closed in the same business run and can be downloaded for review.",
    "active.boundary.scopeTitle": "Inspect scope and publication boundaries",
    "active.boundary.productionScope": "Distributed production AgentTeams and real enterprise ROI still require enterprise-environment validation. Agent candidates and internal approval do not establish customer publication or acceptance.",
    "active.boundary.golden": "The change is in effect. The Quote is linked to its approval record.",
    "active.boundary.goldenFormation": "The Quote is ready. Required approvals and application records are being checked.",
    "active.boundary.pending": "This interaction is driven by {profile}; the active business run has not formed yet. The control plane can form a Quote only after the AgentTeams candidate result, Reviewer, Tool, and Skill pass integrity checks. Later high-risk changes still require explicit approval from the matching Owner.",
    "active.boundary.active": "This interaction is driven by {profile}; the Quote has formed and reached {stage}. Agents, Reviewers, Tools, and Skills produce candidates or evidence only. Later high-risk changes still require explicit approval from the matching Owner.",
    "active.boundary.oac": "The admitted OAC activation binding was consumed in the same Quote-formation transaction, and the run has reached {stage}; the formation result and read-only Tool receipt are durable. OAC admission is not business approval, so later high-risk changes still require an explicit click from the designated Owner.",
    "active.boundary.complete": "The current result and its completion receipts are verified. Changes, approvals, and rebuilds reflect this run's actual records. This system does not manage customer publication or acceptance; local records do not establish distributed production deployment or real-enterprise ROI validation.",
    "active.boundary.initial": "The active business run has not formed yet. The control plane can form a Quote only after the AgentTeams candidate result, Reviewer, Tool, and Skill pass integrity checks. Later high-risk changes still require explicit approval from the matching Owner.",
    "active.boundary.unavailable": "No authoritative Workspace state was loaded. The UI has failed closed and does not reinterpret a request failure as a new task or an empty Workspace.",
    "active.profileAdmitted": "admitted Enterprise Quote Pack",
    "fallback.packClosure": "Verify Pack / Profile / Source / Authority closure",
    "fallback.formation": "Form the four-domain Coalition, Context, and collaborative working draft",
    "fallback.toolRead": "{tool} · read complete dependency evidence",
    "fallback.previewLock": "{kind} · lock the impact Preview and Minimal Rebase Certificate",
    "fallback.approval": "{kind} · explicitly approve the reviewed locked Preview",
    "fallback.rebase": "{kind} · selective Rebase → successor internal revision",
    "error.request": "Request failed (HTTP {status})",
    "error.apiCode": "Request could not be completed (code: {code})",
    "error.approvalWindow": "The approval time window is invalid. Check the server clock, preview again, and approve the new preview.",
    "error.approvalTimestamp": "The approval timestamp format is invalid. Check the timestamp format, preview again, and approve the new preview.",
    "error.incident": "Error reference: {id}",
    "error.previewDigest": "Locked preview digest is missing. Execution stopped.",
    "error.approvalDigest": "Valid approval digest is missing. Execution stopped.",
    "toast.reviewFirst": "Complete the preview review first ({seconds}s).",
    "toast.recordingApproval": "Recording human approval…",
    "toast.generatingReceipt": "Generating a verifiable receipt…",
    "toast.applyingApproved": "Human approval recorded; the deterministic control plane is applying it…",
    "toast.approvedAndApplied": "Human approval bound; selective Rebase completed automatically",
    "toast.completed": "{action}: complete",
    "toast.serverGate": "The server review gate is still waiting: {seconds}s · {notBefore}",
    "toast.downloaded": "{name} downloaded",
    "toast.oacGateOpened": "OAC enterprise adaptation is open. Complete its six admission stages.",
    "header.controlledLocal": "LOCAL VALIDATION",
    "header.declaredData": "DATA CLASS: LOADING",
    "header.syntheticData": "ORGANIZATION: CONTROLLED ENTERPRISE SAMPLE",
    "header.syntheticPricedData": "CONTROLLED ENTERPRISE SAMPLE · PRICING INPUT SOURCES IN QUOTE",
    "header.dataUnavailable": "DATA CLASS: STATE UNAVAILABLE",
    "header.mechanismChecking": "RUN CHECK: CHECKING",
    "header.mechanismPass": "RUN CHECK: PASS",
    "header.mechanismStatus": "RUN CHECK: {status}",
    "header.currentTask.waiting": "ACTIVE TASK: AWAITING INTAKE",
    "header.currentTask.progress": "ACTIVE TASK: {stage}",
    "header.currentTask.complete": "ACTIVE TASK: COMPLETED",
    "header.currentTask.unavailable": "ACTIVE TASK: STATE UNAVAILABLE",
    "header.goldenPilot": "ORCHESTRATION: AGENTTEAMS",
    "header.realEnterpriseNotRun": "PRODUCTION READY: NO · ENTERPRISE VALIDATION PENDING",
    "hero.scenario": "ENTERPRISE WORKSPACE / ENTERPRISE QUOTE",
    "complexity.solution.kicker": "03 · ORGREBASE SOLUTION",
    "journey.kicker": "DURABLE WORKFLOW",
    "timeline.productOwner": "Product Owner",
    "timeline.financeOwner": "Finance Owner",
    "cockpit.kicker": "ONE SCENARIO · LIVE RUN",
    "scene.activeRun": "ACTIVE RUN",
    "scene.goldenRun": "Business end-to-end run",
    "scene.stage": "STAGE",
    "scene.packProfile": "PACK / PROFILE",
    "scene.authority": "CANONICAL AUTHORITY",
    "value.kicker": "CHANGE RESPONSE · SINGLE PRIMARY USER",
    "value.primaryRole": "Quote Operations Owner",
    "value.process.initial": "EIGHT-STEP RESPONSIBILITY CHAIN CONFIGURED",
    "value.cost.evidence": "MODELLED COUNTERFACTUAL",
    "value.cost.boundary": "NORMALIZED ACTION COST · NOT ENTERPRISE ROI",
    "collaboration.currentRun": "CURRENT BUSINESS RUN",
    "collaboration.boundary.initial": "ACTIVE · WAITING FOR CHANGE-DRIVEN CASE",
    "collaboration.terminal.kicker": "TERMINAL DELIVERABLE",
    "retained.kicker": "HISTORICAL RELIABILITY VERIFICATION",
    "retained.boundary.initial": "VALIDATED · LOCAL · PINNED IN-PROCESS · NOT LIVE DISTRIBUTED",
    "retained.authority": "AUTHORITY",
    "retained.runBoundary": "RUN BOUNDARY",
    "retained.exceptions": "CONTROL EXCEPTIONS",
    "retained.exceptions.detail": "conflict · reassign · late fencing",
    "retained.successor": "GOVERNED SUCCESSOR",
    "retained.ledger.title": "AgentTeams Native Action Ledger",
    "retained.ledger.loading": "Reading and validating run records…",
    "table.tool": "TOOL",
    "operations.apiHealth": "API HEALTH",
    "operations.workspaceReady": "WORKSPACE READY",
    "operations.eventChain": "WORKSPACE GLOBAL AUDIT CHAIN",
    "operations.persistence": "PERSISTENCE",
    "operations.sqliteLocal": "SQLITE · LOCAL",
    "operations.delivery.scope": "HTTP · OTLP · DEPLOYMENT · SUPPLY CHAIN",
    "operations.sourceTool": "SOURCE + TOOL",
    "operations.sourceTool.detail": "real HTTP protocol ≠ enterprise connector",
    "operations.otlpFields": "OTLP KEY FIELDS",
    "operations.deploySupply": "DEPLOY / SBOM / GUIDE",
    "operations.failure.kill.detail": "separate controlled-local run · retry intent / adopt committed result",
    "quote.kicker": "CURRENT DELIVERABLE",
    "impact.kicker": "IMPACT DECISION · PRE-APPROVAL",
    "impact.previewDigest": "Preview evidence",
    "impact.minimalCertificate": "VMRC certificate binding",
    "impact.previewEvidenceBound": "BOUND TO THIS PREVIEW",
    "impact.certificateVerified": "VMRC EXACT BINDING VERIFIED",
    "impact.certificateInvalid": "INVALID VMRC BINDING · APPLY REJECTED",
    "governance.kicker": "GOVERNANCE & RECOVERY",
    "governance.approvalDigest": "Approval digest",
    "governance.successorGraph": "Successor graph",
    "governance.recovery": "Recovery",
    "governance.dependencyTool": "Dependency tool",
    "governance.toolReceipt": "Tool receipt",
    "governance.approvalsBound": "{count} HUMAN APPROVAL RECEIPTS BOUND",
    "governance.successorReady": "SUCCESSOR STATE ESTABLISHED",
    "governance.toolReceiptBound": "READ-ONLY TOOL RECEIPT BOUND",
    "governance.eventChain": "EVENT CHAIN",
    "enum.empty": "EMPTY",
    "enum.notFormed": "NOT FORMED",
    "enum.notRun": "AWAITING EXECUTION",
    "enum.stale": "STALE AGAINST CURRENT RUN",
    "enum.notObserved": "NOT OBSERVED",
    "enum.unavailable": "UNAVAILABLE",
    "enum.waiting": "WAITING",
    "enum.checking": "CHECKING",
    "enum.pass": "PASS",
    "enum.fail": "FAIL",
    "enum.critical": "CRITICAL",
    "enum.validated": "VALIDATED",
    "enum.partial": "PARTIAL",
    "enum.completed": "COMPLETED",
    "enum.trustedComplete": "TRUSTED COMPLETE",
    "enum.advisoryAccepted": "ADVISORY ACCEPTED",
    "enum.reviewRequired": "REVIEW REQUIRED",
    "enum.abstain": "ABSTAIN",
    "enum.replan": "REPLAN",
    "enum.succeeded": "SUCCEEDED",
    "enum.success": "SUCCESS",
    "enum.executed": "EXECUTED",
    "enum.fenced": "FENCED",
    "enum.differentRun": "SEPARATE FROM ACTIVE BUSINESS RUN",
    "enum.sameRetainedRun": "SAME RETAINED SUCCESSOR RUN",
    "enum.installedWheel": "WHEEL INSTALLED",
    "enum.rollbackDecisionRecorded": "ROLLBACK DECISION RECORDED",
    "enum.executedAndInvoked": "EXECUTED AND INVOKED",
    "enum.signalsExported": "TRACES, LOGS, AND METRICS EXPORTED AND QUERIED",
    "enum.approved": "APPROVED",
    "enum.rejected": "REJECTED",
    "enum.locked": "LOCKED",
    "enum.governed": "GOVERNED",
    "enum.committed": "COMMITTED",
    "enum.verified": "VERIFIED",
    "enum.validatedAtomicRollback": "CONTROLLED-LOCAL ATOMIC ROLLBACK VALIDATED",
    "enum.validatedDeterministicSeparateRun": "LOCAL DETERMINISTIC COMPENSATION VALIDATED (SEPARATE RUN)",
    "enum.validatedRealToolSeparateRun": "LOCAL REAL GIT COMPENSATION VALIDATED (SEPARATE RUN)",
    "enum.candidateDelivered": "CANDIDATE DELIVERED",
    "enum.canary": "CONTROLLED CANARY DRY-RUN",
    "enum.current": "CURRENT",
    "enum.healthy": "HEALTHY",
    "enum.ready": "READY",
    "enum.true": "TRUE",
    "enum.false": "FALSE",
    "enum.none": "NONE",
    "enum.noCandidate": "NO CANDIDATE",
    "enum.notApplicable": "NOT APPLICABLE",
    "enum.improve": "IMPROVE",
    "enum.candidateOnly": "CANDIDATE ONLY",
    "enum.readOnly": "READ ONLY",
    "enum.canonicalWrite": "CANONICAL WRITE",
    "enum.approval": "APPROVAL",
    "enum.controlledLocal": "CONTROLLED LOCAL",
    "enum.syntheticFixture": "CONTROLLED DEMO DATA",
    "enum.singleRunSeed": "SINGLE_RUN_SEED",
    "enum.failClosed": "FAIL CLOSED",
    "enum.startupBlocked": "BLOCK",
    "enum.localDeterministic": "LOCAL DETERMINISTIC",
    "type.changeSetRevision": "CHANGE SET REVISION",
    "type.impactPreview": "IMPACT PREVIEW",
    "enum.persistedReopenable": "PERSISTED · COMPLETED STATE REOPENABLE",
    "enum.validatedProxy": "VALIDATED PROXY",
    "enum.modelled": "MODELLED",
    "enum.referenceTemplate": "REFERENCE TEMPLATE",
    "enum.designTargetBaseline": "REFERENCE HUMAN BASELINE · ENTERPRISE CALIBRATION PENDING",
    "enum.draft": "DRAFT",
    "enum.evaluated": "EVALUATED",
    "enum.shadow": "CONTROLLED CANDIDATE VERIFICATION",
    "enum.release": "RELEASE",
    "enum.notTriggered": "NOT TRIGGERED",
    "enum.decisionOnly": "DECISION ONLY",
    "enum.requalificationRequired": "REQUALIFICATION REQUIRED",
    "enum.packageResources": "PACKAGE RESOURCES",
    "enum.present": "PRESENT",
    "enum.launchPreviewed": "PRODUCT-DATE CHANGE AWAITING APPROVAL",
    "enum.quoteV1": "INTERNAL WORKING DRAFT FORMED",
    "enum.quoteV2": "PRODUCT-DATE CHANGE APPLIED",
    "enum.quoteV3": "INTERNAL CHANGE LOOP VERIFIED",
    "enum.launchApproved": "PRODUCT-DATE CHANGE APPROVED · AWAITING APPLY",
    "enum.currencyPreviewed": "CURRENCY CHANGE AWAITING APPROVAL",
    "enum.currencyApproved": "CURRENCY CHANGE APPROVED · AWAITING APPLY",
    "enum.retryIntent": "RETRY INTENT",
    "enum.adoptCommitted": "ADOPT COMMITTED",
    "enum.controlled": "CONTROLLED",
    "enum.canonicalApplied": "CANONICAL APPLIED",
    "enum.nativeExecuted": "AGENTTEAMS NATIVE CONTROL PLANE EXECUTED",
    "enum.deterministicProviders": "DETERMINISTIC DOMAIN PROVIDERS",
    "enum.independentExecutors": "PINNED TEAMHARNESS + INDEPENDENT EXECUTOR PROCESSES",
    "enum.targetWrites": "{count} TARGET WRITES",
    "enum.awaitingApproval": "AWAITING HUMAN APPROVAL",
    "enum.approvedCanary": "APPROVED FOR CONTROLLED CANARY DRY-RUN",
    "enum.waitingQuoteV3": "WAITING FOR INTERNAL CHANGE LOOP TERMINAL",
    "actionLedger.applyQuote": "APPLY QUOTE CANDIDATE",
    "authority.canonical": "OrgRebase canonical-state control plane",
    "enum.actions": "{count} ACTIONS",
    "enum.processes": "{count} PROCESSES",
    "enum.steps": "{count} STEPS",
    "enum.events": "{count} EVENTS",
    "enum.writes": "{count} WRITES",
    "enum.attempt": "ATTEMPT {count}",
    "type.auto": "AUTO",
    "type.candidate": "CANDIDATE",
    "type.human": "HUMAN",
    "type.write": "WRITE",
    "type.tool": "TOOL",
    "actionLedger.admitClosure": "Admit Pack, Profile, Source, and authority closure",
    "actionLedger.nativeCandidate": "Execute native AgentTeams candidate taskflow",
    "actionLedger.reviewProvenance": "Replan, then accept exact provenance",
    "actionLedger.recoverFinance": "Recover Finance dependency evidence",
    "actionLedger.composeCandidate": "Compose reviewed quote candidate",
    "actionLedger.formQuote": "Form domain coalition, context, and collaborative working draft",
    "actionLedger.readDependency": "Read dependency evidence",
    "actionLedger.bindAdvisory": "Bind the admitted change to advisory tasks",
    "actionLedger.explainPremise": "Explain the changed authority-domain premise without admitting effects",
    "actionLedger.explainImpact": "Explain quote impact candidates without changing canonical state",
    "actionLedger.taskGraph": "Task graph candidate",
    "actionLedger.semanticExplanation": "Semantic explanation candidate",
    "actionLedger.impactCandidate": "Impact-analysis candidate",
    "actionLedger.lockLaunch": "Lock launch-date change preview",
    "actionLedger.approveLaunch": "Approve launch-date preview",
    "actionLedger.applyLaunch": "Apply launch-date selective Rebase",
    "actionLedger.lockCurrency": "Lock currency-change preview",
    "actionLedger.approveCurrency": "Approve currency preview",
    "actionLedger.applyCurrency": "Apply currency selective Rebase",
    "status.dagCompleted": "DAG PLANNED · COMPLETED",
    "status.priceMissing": "ABSTAIN · PRICE BAND MISSING",
    "status.candidateAccepted": "CANDIDATE ACCEPTED",
    "status.candidateAcceptedCode": "CANDIDATE_ACCEPTED",
    "status.governedAppliedCode": "GOVERNED_APPLIED",
    "status.controlledLocalCommandCode": "CONTROLLED_LOCAL_SCRIPTED_COMMAND",
    "status.projectCompleted": "PROJECT completed",
    "status.recoveredCandidate": "RECOVERED · CANDIDATE ACCEPTED",
    "status.releaseCanary": "RELEASE · CANARY · 8/8",
    "status.atomicCommit": "VERIFIED · ATOMIC COMMIT",
    "status.waitingCommit": "VERIFIED · WAITING COMMIT",
    "status.explicitApproval": "EXPLICIT APPROVAL",
    "status.waitingClick": "WAITING FOR CLICK",
    "status.nextRiskWaits": "NEXT HIGH-RISK CHANGE WAITS",
    "status.contextOnly": "CONTEXT ONLY · NO WORKER RUN OBSERVED",
    "status.previewLocked": "PREVIEW LOCKED",
    "status.waitingCandidates": "WAITING FOR CANDIDATES",
    "status.explicitLocalApproval": "EXPLICIT LOCAL APPROVAL",
    "status.waitingHumanClick": "WAITING FOR HUMAN CLICK",
    "status.noCoordinator": "NO COORDINATOR RUN OBSERVED",
    "status.noBinding": "NO BINDING OBSERVED",
    "experience.authority.open": "DISCOVER · LOAD · INVOKE",
    "experience.authority.locked": "CANDIDATE ONLY · LOCKED",
    "maturity.signedSynthetic": "SIGNED SYNTHETIC BASELINE",
    "maturity.inProgress": "IN PROGRESS",
    "maturity.nativeControl": "CONTROLLED-LOCAL NATIVE CONTROL",
    "maturity.independentProcess": "CONTROLLED-LOCAL INDEPENDENT PROCESS",
    "maturity.realHttp": "CONTROLLED-LOCAL REAL HTTP",
    "maturity.releaseAuthority": "SAME-RUN CONTROLLED-LOCAL RELEASE AUTHORITY",
    "maturity.highFidelitySynthetic": "HIGH-FIDELITY SYNTHETIC",
    "maturity.serverApproval": "SERVER-PERSISTED HUMAN APPROVAL",
    "maturity.browserIncomplete": "BROWSER OR RUN INCOMPLETE",
    "maturity.frozenPack": "SEALED LOCAL AUDIT PACK",
    "maturity.freezePending": "ACTIVE-RUN AUDIT PACK PENDING",
    "maturity.videoPending": "AWAITING VIDEO APPROVAL",
    "maturity.controlledTested": "CONTROLLED-LOCAL TESTED",
    "maturity.completedReopen": "CONTROLLED-LOCAL COMPLETED-RUN REOPEN",
    "maturity.runScoped": "RUN-SCOPED AUDIT RECORD",
    "maturity.controlledObserved": "CONTROLLED-LOCAL OBSERVED",
    "maturity.retainedMvp": "RETAINED MVP OPERATIONS RECORD",
    "role.quoteOperator": "Quote Operations Owner",
    "role.productAgent": "Product Agent",
    "role.legalAgent": "Legal Agent",
    "role.financeAgent": "Finance Agent",
    "role.gtmAgent": "GTM Agent",
    "role.gtmOwner": "GTM Owner",
    "role.productOwner": "Product Owner",
    "role.legalOwner": "Legal Owner",
    "role.financeOwner": "Finance Owner",
    "role.deterministicControl": "Deterministic Control",
    "role.changedSourceOwner": "Changed-source Owner",
    "role.boundedRuntime": "Bounded Runtime",
    "role.canonicalWriter": "Canonical Writer",
    "role.exactApprovalOwner": "Exact Approval Owner",
    "business.organization.evergreen": "Evergreen Industries",
    "business.customer.blueHarbor": "Blue Harbor customer",
    "business.skill.quoteCompose": "Enterprise quote composition Skill",
    "business.role.quoteOperator": "Quote Operations Owner",
    "business.role.productOwner": "Evergreen Product Owner",
    "business.role.financeOwner": "Evergreen Finance Owner",
    "business.role.skillSteward": "Skill Steward",
    "business.role.controlPlane": "OrgRebase deterministic control plane",
    "business.role.readOnlyEvidence": "Run-scoped read-only operations view",
    "business.role.packAdmission": "Quote Pack admission control",
    "business.role.workspaceFormation": "Workspace formation control",
    "business.role.impactControl": "Change-impact control",
    "business.role.productSteward": "Product domain worker",
    "business.role.legalSteward": "Legal domain worker",
    "business.role.financeSteward": "Finance domain worker",
    "business.role.gtmSteward": "GTM domain worker",
    "business.role.changeCoordinator": "Change Coordinator",
    "business.role.quoteReviewer": "Quote Reviewer",
    "business.role.independentReviewer": "Independent Reviewer",
    "business.role.domainCandidate": "Domain candidate worker",
    "business.role.exactDomainOwner": "Exact change-domain owner",
    "business.actor.productStewardLocal": "@product-steward:controlled.local",
    "business.actor.legalStewardLocal": "@legal-steward:controlled.local",
    "business.actor.financeStewardLocal": "@finance-steward:controlled.local",
    "business.actor.gtmStewardLocal": "@gtm-steward:controlled.local",
    "authority.stateStoreOnlyCode": "STATESTORE_REBASE_WORKFLOW_ONLY",
    "business.quote.blueHarbor": "Blue Harbor Enterprise Quote",
    "business.scenario.evergreen": "Evergreen Industries enterprise quote evolution",
    "business.plan.evergreenPlus": "Evergreen Enterprise Plus",
    "business.residency.usEu": "US and EU regions supported",
    "business.priceBand.strategic": "Strategic customer price band",
    "business.terms.legalReview": "Legal review required",
    "business.deliverable.enterpriseQuote": "Enterprise Quote",
    "business.impact.financeApproval": "Enterprise Quote Finance Approval",
    "business.impact.launchReadiness": "Enterprise Launch Readiness Review",
    "business.impact.partnerBrief": "Enterprise Partner Brief",
    "business.change.launchDate": "Product launch-date change",
    "business.change.currency": "Finance currency-policy change",
    "business.change.generic": "Business change",
    "business.reason.coverageNoPath": "Complete dependency coverage; no admitted impact path",
    "business.reason.insufficientCoverage": "Dependency evidence is insufficient; human review required",
    "business.reason.admittedPath": "A verifiable dependency path was admitted",
    "business.reason.digestMismatch": "Result digest mismatch",
    "business.reason.staleAttempt": "Stale attempt fenced",
    "business.relation.requiresClaim": "Requires product claim",
    "business.relation.requiresPolicy": "Requires finance policy",
    "business.evidence.syntheticGold": "Controlled demo benchmark",
    "business.evidence.localProduct": "Observed local product path",
    "machine.model.deterministicManager": "NONE · DETERMINISTIC CONTROL-PLANE COORDINATOR",
    "machine.model.deterministicWorker": "NONE · DETERMINISTIC DOMAIN WORKER",
    "machine.model.vertexAdvisoryBoundary": "LIVE VERTEX STRUCTURED ADVISORY · NOT ACCEPTANCE AUTHORITY · NO CANONICAL WRITE",
    "machine.mode.nativeTaskflow": "CONTROLLED-LOCAL NATIVE TASKFLOW",
    "machine.mode.goldenE2E": "CONTROLLED LOCAL BUSINESS END-TO-END RUN",
    "machine.mode.localApproval": "LOCAL EXPLICIT WORKSPACE APPROVAL",
    "machine.mode.deterministicControl": "DETERMINISTIC CONTROL PLANE",
    "machine.mode.governedSuccessor": "CONTROLLED-LOCAL GOVERNED SUCCESSOR",
    "machine.mode.notExternalValidation": "NOT EXTERNAL HUMAN VALIDATION",
    "machine.tool.dependencyEvidence": "DEPENDENCY EVIDENCE TOOL · CONTROLLED-LOCAL REAL HTTP",
    "machine.tool.stateStoreTransaction": "StateStore atomic transaction",
    "machine.tool.sqliteEventTransaction": "SQLite transaction + event chain",
    "machine.skill.noneAtWrite": "NO SKILL AT CANONICAL WRITE BOUNDARY",
    "machine.trace.otlpCorrelated": "OTLP CORRELATED · TRACE ID NOT PROJECTED",
    "machine.project.notObserved": "NOT OBSERVED AT PROJECT NODE",
    "machine.tool.controlledHttp": "CONTROLLED HTTP TOOL",
    "machine.skill.upstreamCandidate": "enterprise-quote-compose UPSTREAM OF CANDIDATE",
    "machine.deployment.controlledLocal": "CONTROLLED-LOCAL DEPLOYMENT PROFILE · INITIAL",
    "machine.tool.vertex37AdvisoryVerifier": "vertex-ai:gemini-3.7-flash advisory + deterministic verifier",
    "machine.tool.vertex38AdvisoryVerifier": "vertex-ai:gemini-3.8-flash advisory + deterministic verifier",
    "system.crmCpqQueue": "CRM / CPQ request queue",
    "system.productSource": "Product catalog / release source",
    "system.legalSource": "CLM / restricted Legal source",
    "system.financeSource": "ERP / pricing-policy source",
    "system.quoteComposer": "Document / CPQ composer",
    "system.quoteOutput": "CPQ / document output",
    "system.manualReview": "Manual cross-system change review",
    "system.manualRework": "Manual document and system rework",
    "unit.minute": "minute",
    "unit.businessHour": "business hour",
    "unit.businessDay": "business day",
    "spine.pack": "PACK ADMISSION",
    "spine.source": "SOURCE",
    "spine.agentTeams": "AgentTeams",
    "spine.skill": "Skill",
    "spine.governedApply": "GOVERNED APPLY",
    "spine.workers": "DOMAIN WORKERS",
    "spine.review": "INDEPENDENT REVIEW",
    "spine.formation": "WORKSPACE FORMATION",
    "spine.human": "HUMAN GATE",
    "spine.rebase": "SELECTIVE REBASE",
    "spine.quote": "QUOTE DELIVERABLE",
    "spine.evidence": "EVIDENCE CLOSURE",
    "spine.preview": "IMPACT PREVIEW",
    "spine.apply": "GOVERNED APPLY",
    "spine.terminal": "TERMINAL",
    "spine.lifecycle": "NATIVE LIFECYCLE",
    "spine.retained": "RETAINED EVIDENCE",
    "spine.detail.synthetic": "controlled demo data · declared",
    "spine.detail.nativeControl": "pinned native control plane",
    "spine.detail.candidateWrites": "candidate-only · 0 target writes",
    "spine.detail.advisory": "ADVISORY ONLY",
    "spine.detail.financeRecovery": "Finance missing-fact recovery",
    "spine.detail.formationReceipt": "controlled AgentTeams formation receipt",
    "spine.detail.explicitClick": "waiting for explicit owner click",
    "spine.detail.stateStore": "canonical-state control plane only",
    "spine.detail.deliverable": "enterprise deliverable",
    "spine.detail.strictAdmission": "strict admission",
    "spine.detail.domainContext": "four-domain context",
    "spine.detail.zeroWrite": "zero writes",
    "spine.detail.validationFailed": "digest validation failed",
    "spine.detail.failClosed": "fail closed; no inference",
    "field.dependsOn": "DEPENDS ON",
    "field.inputRefs": "INPUT REFS",
    "field.inputDigest": "INPUT DIGEST",
    "field.digest": "DIGEST",
    "field.outputDigest": "OUTPUT DIGEST",
    "field.model": "MODEL",
    "field.modelProvider": "MODEL PROVIDER",
    "field.providerRequest": "PROVIDER REQUEST",
    "field.modelAuthority": "MODEL AUTHORITY",
    "field.tool": "TOOL",
    "field.skill": "SKILL",
    "field.trace": "TRACE",
    "field.candidateOnly": "CANDIDATE ONLY",
    "field.targetWrites": "TARGET WRITES",
    "field.evidenceMode": "EVIDENCE / MODE",
    "topology.role.workerGeneric": "WORKER · {domain}",
    "topology.role.controlAcceptance": "CONTROL ACCEPTANCE · NO INDEPENDENT REVIEWER",
    "topology.role.humanOwner": "HUMAN OWNER",
    "topology.role.canonicalState": "CANONICAL STATE",
    "topology.role.coordinator": "CONTROL-PLANE TASK COORDINATOR",
    "topology.role.projectBoundary": "COORDINATOR / PROJECT BOUNDARY",
    "topology.name.quoteTaskCoordinator": "Quote Task Coordinator",
    "topology.auditIdentifier": "EXACT EXECUTION IDENTIFIER",
    "topology.role.humanCommand": "HUMAN OWNER COMMAND BOUNDARY",
    "topology.stage.coordinate": "01 · COORDINATE",
    "topology.stage.domainWorkers": "02 · DOMAIN WORKERS",
    "topology.stage.accept": "03 · ACCEPT",
    "topology.stage.humanShort": "04 · HUMAN",
    "topology.stage.write": "05 · WRITE",
    "topology.name.formation": "Controlled AgentTeams Formation",
    "topology.name.acceptance": "OrgRebase deterministic acceptance control",
    "topology.name.localCommands": "scripted local domain-owner commands",
    "topology.detail.declarative": "NONE · DECLARATIVE PROGRAM",
    "topology.detail.failClosedVerifier": "NONE · FAIL-CLOSED VERIFIER",
    "topology.detail.onlyWriter": "NONE · ONLY CANONICAL WRITER",
    "topology.detail.deterministic": "NONE · DETERMINISTIC CONTROL",
    "topology.detail.localCommand": "LOCAL COMMAND",
    "topology.detail.ownerCommand": "OWNER COMMAND",
    "topology.detail.serverWait": "SERVER WAIT {milliseconds}ms",
    "selective.round1": "ROUND 1 · COLLABORATIVE DRAFT → LAUNCH-DATE CONFIRMED DRAFT",
    "selective.round2": "ROUND 2 · LAUNCH-DATE CONFIRMED DRAFT → CURRENCY CONFIRMED DRAFT",
    "selective.launchDate": "launch date",
    "selective.currency": "currency",
    "selective.rebuilt": "rebuilt",
    "selective.preserved": "preserved",
    "selective.unknown": "unknown",
    "selective.falseInvalidations": "false invalidations",
    "selective.unauthorized": "unauthorized disclosures",
    "selective.approvalDigest": "approval digest",
    "selective.auditDetails": "View approval receipt",
    "selective.final": "{quote} · launch {launch} · currency {currency}",
    "value.process.status": "{count} STEPS · RACI MAPPED",
    "value.target.design": "REFERENCE HUMAN BASELINE · CALIBRATION PENDING",
    "value.metricEvidence": "VALUE EVIDENCE",
    "value.failClosed": "FAIL CLOSED",
    "value.noRoiClaim": "NO ROI CLAIM",
    "value.cost.detail": "NORMALIZED ACTION COST · NOT ENTERPRISE ROI<br>declared {lower}–{upper} · stress {stressLower}–{stressUpper}",
    "retained.authority.value": "AgentTeams CANDIDATE → {state}",
    "retained.exceptions.value": "CONFLICT {conflict} · REASSIGN {reassign} · LATE RESULT FENCING {late}<br>ATOMIC ROLLBACK {atomic} · DOWNSTREAM STATE COMPENSATION {downstream} · REVERSIBLE GIT COMPENSATION {git}<br>{enterprise} · {residuals} RESIDUAL EFFECTS · {writes} READ-MODEL WRITES · SEPARATE MECHANISM EVIDENCE, NOT MERGED INTO THE ACTIVE BUSINESS RUN",
    "retained.exceptions.enterprisePending": "REAL ENTERPRISE CONNECTOR COMPENSATION NOT CONNECTED",
    "retained.successor.value": "{quote} · {terminal} · {approval} · {writes} canonical writes",
    "retained.skill.header": "SKILL {version} · {verdict}",
    "retained.skill.evaluation": "{cases} eval partitions · {passed}/{total} admission gates · {mode} · release receipt bound",
    "retained.skill.requal": "REQUAL {verdict} · {state} / {mode}",
    "retained.skill.invocation": "DISCOVERED → WHEEL LOADED → INVOKED WITH RECEIPT",
    "retained.skill.technical": "VIEW TECHNICAL RECEIPTS",
    "retained.skill.unavailable": "This task has no separate Skill-release lifecycle summary; the published sources below come directly from the capability registry.",
    "retained.ledger.count": "{actions} actions · {bindings} bindings",
    "retained.ledger.fail": "FAIL CLOSED",
    "retained.boundary.value": "VALIDATED · SEPARATE LOCAL RUN · PINNED IN-PROCESS · NOT LIVE DISTRIBUTED",
    "retained.runLabel": "SEPARATE RUN VALIDATED",
    "operations.card.alert": "ALERT RULE VALIDATION",
    "operations.card.retention": "RETENTION PRUNE EXERCISE",
    "operations.card.query": "OTLP RETENTION QUERY",
    "operations.card.capacity": "LOCAL SEQUENTIAL CAPACITY SMOKE",
    "operations.card.backup": "QUIESCENT SINGLE-HOST BACKUP / RESTORE",
    "operations.card.recovery": "SEPARATE RUN · SIGKILL RECOVERY",
    "operations.sameRun.runLabel": "SAME-RUN EVIDENCE CORRELATED",
    "operations.card.receiptBound": "AUDIT RECEIPT BOUND",
    "operations.card.alerts": "{count} alerts",
    "operations.card.alertsVerified": "healthy chain passed ({healthy} current alerts) · failure probe caught {negative} {severity} · external delivery not run",
    "operations.card.retentionDetail": "{deleted} expired test records deleted · business evidence retained · long-window execution pending",
    "operations.card.queryDetail": "traces / logs / metrics in single-host SQLite",
    "operations.card.capacityDetail": "{successes} sequential calls succeeded · p95 {p95}ms · not concurrent / not an SLA",
    "operations.card.backupDetail": "{count} local artifacts · digest {match} · not geo-DR",
    "operations.card.localVerified": "LOCAL VALIDATION COMPLETE",
    "operations.card.singleHostVerified": "SINGLE-HOST RESTORE COMPLETE",
    "operations.card.completedStateRecovered": "TWO CONTROLLED-LOCAL RECOVERIES PASSED",
    "operations.card.match": "match",
    "operations.card.unknown": "unknown",
    "operations.card.recoveryDetail": "{count} SIGKILL · SEPARATE CONTROLLED-LOCAL RUN · {dispositions} · DISTRIBUTED RECOVERY REQUIRES PRODUCTION VALIDATION",
    "operations.integrations": "LOCAL SOURCE HTTP {source} · LOCAL TOOL HTTP {tool} · 0 EXTERNAL TARGET WRITES · ENTERPRISE CONNECTORS REQUIRE DEPLOYMENT CONFIGURATION",
    "operations.sameRun.externalTargetWrites": "{count} EXTERNAL TARGET WRITES",
    "operations.sameRun.externalTargetWritesUnobserved": "External target-write count not separately observed",
    "operations.sameRun.internalCanonicalWrite": "Internal canonical state written",
    "plane.control": "CONTROL PLANE",
    "operations.otlp.summary": "QUERY {status} · trace, execution, task, Skill, Tool receipt, and writes correlated · {count} attrs",
    "operations.readiness.enterpriseValidation": "REQUIRES ENTERPRISE PRODUCTION VALIDATION",
    "operations.readiness.summary": "frozen OrgRebase {version} artifact · {deployment} · {sbom} {components} lockfile components · CONTRIBUTING {guide} · production SLA {sla} · GEO-DR {geoDr}",
  }),
});

let currentLanguage = DEFAULT_LANGUAGE;

function t(key, params = {}, language = currentLanguage) {
  const catalog = I18N[language] || I18N[DEFAULT_LANGUAGE];
  const fallback = I18N[DEFAULT_LANGUAGE][key];
  const template = catalog[key] ?? fallback ?? key;
  return String(template).replace(/\{([a-zA-Z0-9_]+)\}/g, (_, name) => String(params[name] ?? `{${name}}`));
}

function modelSuggestion(provider, evidenceClass) {
  const normalizedProvider = String(provider || "").toLowerCase();
  const normalizedEvidence = String(evidenceClass || "").toUpperCase();
  if ((normalizedProvider.includes("vertex") || normalizedProvider.includes("gemini"))
    && (normalizedEvidence.includes("LIVE_VERTEX") || normalizedEvidence === "LIVE_MODEL")) {
    return t("modelSuggestion.vertexLive");
  }
  if (normalizedProvider.includes("ollama") || normalizedProvider.includes("local")) {
    return t("modelSuggestion.local");
  }
  return t("modelSuggestion.receipt");
}

function eventCount(chain) {
  if (!chain || typeof chain !== "object") return 0;
  if (Number.isFinite(Number(chain.events))) return Number(chain.events);
  if (Array.isArray(chain.records)) return chain.records.length;
  return 0;
}

function projectedEventScopes(state) {
  const legacy = state && state.event_chain || {};
  const scopes = state && state.event_scopes || {};
  const present = Boolean(scopes.quote_business || scopes.workspace_global || scopes.oac_adaptation);
  return {
    present,
    quote: scopes.quote_business || {
      status: "NOT_OBSERVED",
      events: 0,
      definition: "NO_QUOTE_BUSINESS_EVENTS_OBSERVED",
    },
    global: scopes.workspace_global || legacy,
    oac: scopes.oac_adaptation || {},
  };
}

// Machine enums remain authoritative in payloads and downloadable receipts.
// The product surface defaults to natural language; exact tokens stay in the
// source evidence and explicit hover titles instead of dominating Chinese copy.
const DISPLAY_KEY_BY_TOKEN = Object.freeze({
  EMPTY: "enum.empty",
  NOT_FORMED: "enum.notFormed",
  "NOT FORMED": "enum.notFormed",
  NOT_RUN: "enum.notRun",
  LIVE_MODEL: "publicValidation.adaptation.liveModel",
  STALE: "enum.stale",
  NOT_OBSERVED: "enum.notObserved",
  UNAVAILABLE: "enum.unavailable",
  WAITING: "enum.waiting",
  CHECKING: "enum.checking",
  PASS: "enum.pass",
  FAIL: "enum.fail",
  CRITICAL: "enum.critical",
  CREATE: "lifecycle.stage.create",
  DELEGATE: "lifecycle.stage.delegate",
  ACK: "lifecycle.stage.ack",
  HANDOFF_BINDING: "lifecycle.stage.handoff",
  SUBMIT: "lifecycle.stage.submit",
  CHECK: "lifecycle.stage.check",
  ACCEPT: "lifecycle.stage.accept",
  COMPLETE: "lifecycle.stage.complete",
  create_project: "lifecycle.action.createProject",
  delegate_task: "lifecycle.action.delegateTask",
  ack_task: "lifecycle.action.ackTask",
  context_projection_digest: "lifecycle.action.bindContext",
  submit_task: "lifecycle.action.submitTask",
  check_task: "lifecycle.action.checkTask",
  accept_task_result: "lifecycle.action.acceptTask",
  complete_project: "lifecycle.action.completeProject",
  VALIDATED: "enum.validated",
  PARTIAL: "enum.partial",
  COMPLETED: "enum.completed",
  completed: "enum.completed",
  TRUSTED_COMPLETE: "enum.trustedComplete",
  ADVISORY_ACCEPTED: "enum.advisoryAccepted",
  REVIEW_REQUIRED: "enum.reviewRequired",
  ABSTAIN: "enum.abstain",
  REPLAN: "enum.replan",
  SUCCEEDED: "enum.succeeded",
  SUCCESS: "enum.success",
  EXECUTED: "enum.executed",
  FENCED: "enum.fenced",
  DIFFERENT_RUN: "enum.differentRun",
  SAME_RETAINED_RUN: "enum.sameRetainedRun",
  INSTALLED_WHEEL: "enum.installedWheel",
  ROLLBACK_DECISION_RECORDED: "enum.rollbackDecisionRecorded",
  EXECUTED_AND_INVOKED: "enum.executedAndInvoked",
  "3_SIGNALS_EXPORTED_AND_QUERIED": "enum.signalsExported",
  APPROVED: "enum.approved",
  REJECTED: "enum.rejected",
  LOCKED: "enum.locked",
  GOVERNED: "enum.governed",
  COMMITTED: "enum.committed",
  VERIFIED: "enum.verified",
  VALIDATED_CONTROLLED_LOCAL_ATOMIC_ROLLBACK: "enum.validatedAtomicRollback",
  VALIDATED_LOCAL_DETERMINISTIC_SEPARATE_RUN: "enum.validatedDeterministicSeparateRun",
  VALIDATED_LOCAL_REAL_TOOL_SEPARATE_RUN: "enum.validatedRealToolSeparateRun",
  CANDIDATE_DELIVERED: "enum.candidateDelivered",
  CANARY: "enum.canary",
  CURRENT: "enum.current",
  HEALTHY: "enum.healthy",
  READY: "enum.ready",
  TRUE: "enum.true",
  FALSE: "enum.false",
  NONE: "enum.none",
  NOT_APPLICABLE: "enum.notApplicable",
  "N/A": "enum.notApplicable",
  NO_CANDIDATE: "enum.noCandidate",
  IMPROVE: "enum.improve",
  CANDIDATE_ONLY: "enum.candidateOnly",
  READ_ONLY: "enum.readOnly",
  CANONICAL_WRITE: "enum.canonicalWrite",
  APPROVAL: "enum.approval",
  CONTROLLED_LOCAL: "enum.controlledLocal",
  CONTROLLED_LOCAL_AGENTTEAMS: "enum.controlledLocal",
  SYNTHETIC_FIXTURE: "enum.syntheticFixture",
  SINGLE_RUN_SEED: "enum.singleRunSeed",
  FAIL_CLOSED: "enum.failClosed",
  "FAIL CLOSED": "enum.failClosed",
  LOCAL_DETERMINISTIC: "enum.localDeterministic",
  AUTO: "type.auto",
  CANDIDATE: "type.candidate",
  HUMAN: "type.human",
  WRITE: "type.write",
  ChangeSetRevision: "type.changeSetRevision",
  ImpactPreview: "type.impactPreview",
  TOOL: "type.tool",
  AGENTTEAMS: "spine.agentTeams",
  SKILL: "spine.skill",
  DOMAIN_AGENT: "dataJourney.actor.domain",
  DETERMINISTIC_RENDERER: "dataJourney.actor.rendererRole",
  "workspace-renderer": "dataJourney.actor.renderer",
  COORDINATOR: "business.role.changeCoordinator",
  REVIEWER: "business.role.independentReviewer",
  AFFECTED_HARD: "impact.rebuild",
  UNAFFECTED_WITHIN_DECLARED_BOUNDARY: "impact.preserve",
  UNKNOWN: "impact.unknown",
  AFFECTED_REVIEW: "businessChange.unknown",
  AFFECTED_INFORMATIONAL: "businessChange.unknown",
  ADMIT_PACK_PROFILE_SOURCE_AUTHORITY_CLOSURE: "actionLedger.admitClosure",
  EXECUTE_NATIVE_TASKFLOW_CANDIDATE: "actionLedger.nativeCandidate",
  REPLAN_THEN_ACCEPT_EXACT_PROVENANCE: "actionLedger.reviewProvenance",
  RECOVER_FINANCE_DEPENDENCY: "actionLedger.recoverFinance",
  COMPOSE_REVIEWED_QUOTE_CANDIDATE: "actionLedger.composeCandidate",
  FORM_DOMAIN_COALITION_CONTEXT_AND_QUOTE_V1: "actionLedger.formQuote",
  READ_DEPENDENCY_EVIDENCE: "actionLedger.readDependency",
  "bind the admitted workspace change to advisory tasks": "actionLedger.bindAdvisory",
  "explain the changed authority-domain premise without admitting effects": "actionLedger.explainPremise",
  "explain quote impact candidates without changing canonical state": "actionLedger.explainImpact",
  TaskGraph: "actionLedger.taskGraph",
  SemanticExplanation: "actionLedger.semanticExplanation",
  ImpactCandidate: "actionLedger.impactCandidate",
  LOCK_LAUNCH_DATE_PREVIEW: "actionLedger.lockLaunch",
  APPROVE_LAUNCH_DATE_PREVIEW: "actionLedger.approveLaunch",
  APPLY_LAUNCH_DATE_SELECTIVE_REBASE: "actionLedger.applyLaunch",
  LOCK_CURRENCY_PREVIEW: "actionLedger.lockCurrency",
  APPROVE_CURRENCY_PREVIEW: "actionLedger.approveCurrency",
  APPLY_CURRENCY_SELECTIVE_REBASE: "actionLedger.applyCurrency",
  "DAG PLANNED · COMPLETED": "status.dagCompleted",
  "ABSTAIN · PRICE BAND MISSING": "status.priceMissing",
  "CANDIDATE ACCEPTED": "status.candidateAccepted",
  CANDIDATE_ACCEPTED: "status.candidateAcceptedCode",
  GOVERNED_APPLIED: "status.governedAppliedCode",
  CONTROLLED_LOCAL_SCRIPTED_COMMAND: "status.controlledLocalCommandCode",
  "PROJECT completed": "status.projectCompleted",
  "RECOVERED · CANDIDATE ACCEPTED": "status.recoveredCandidate",
  "RELEASE · CANARY · 8/8": "status.releaseCanary",
  "VERIFIED · ATOMIC COMMIT": "status.atomicCommit",
  "VERIFIED · WAITING COMMIT": "status.waitingCommit",
  "EXPLICIT APPROVAL": "status.explicitApproval",
  "WAITING FOR CLICK": "status.waitingClick",
  "NEXT HIGH-RISK CHANGE WAITS": "status.nextRiskWaits",
  "CONTEXT ONLY · NO WORKER RUN OBSERVED": "status.contextOnly",
  "PREVIEW LOCKED": "status.previewLocked",
  "WAITING FOR CANDIDATES": "status.waitingCandidates",
  "EXPLICIT LOCAL APPROVAL": "status.explicitLocalApproval",
  "WAITING FOR HUMAN CLICK": "status.waitingHumanClick",
  "NO COORDINATOR RUN OBSERVED": "status.noCoordinator",
  "NO BINDING OBSERVED": "status.noBinding",
  "DISCOVER · LOAD · INVOKE": "experience.authority.open",
  "CANDIDATE_ONLY · LOCKED": "experience.authority.locked",
  SIGNED_SYNTHETIC_BASELINE: "maturity.signedSynthetic",
  IN_PROGRESS: "maturity.inProgress",
  CONTROLLED_LOCAL_NATIVE_CONTROL: "maturity.nativeControl",
  CONTROLLED_LOCAL_INDEPENDENT_PROCESS: "maturity.independentProcess",
  CONTROLLED_LOCAL_REAL_HTTP: "maturity.realHttp",
  SAME_RUN_CONTROLLED_LOCAL_RELEASE_AUTHORITY: "maturity.releaseAuthority",
  HIGH_FIDELITY_SYNTHETIC: "maturity.highFidelitySynthetic",
  SERVER_PERSISTED_HUMAN_APPROVAL: "maturity.serverApproval",
  BROWSER_OR_RUN_INCOMPLETE: "maturity.browserIncomplete",
  FROZEN_CONTROLLED_LOCAL_PACK: "maturity.frozenPack",
  CURRENT_RUN_FREEZE_PENDING: "maturity.freezePending",
  AWAITING_VIDEO_APPROVAL: "maturity.videoPending",
  CONTROLLED_LOCAL_TESTED: "maturity.controlledTested",
  CONTROLLED_LOCAL_COMPLETED_RUN_REOPEN: "maturity.completedReopen",
  RUN_SCOPED_EVIDENCE: "maturity.runScoped",
  CONTROLLED_LOCAL_OBSERVED: "maturity.controlledObserved",
  RETAINED_MVP_EVIDENCE: "maturity.retainedMvp",
  "Enterprise Quote Operator": "value.primaryRole",
  "Quote Operator": "role.quoteOperator",
  "Product Agent": "role.productAgent",
  "Legal Agent": "role.legalAgent",
  "Finance Agent": "role.financeAgent",
  "GTM Agent": "role.gtmAgent",
  "GTM Owner": "role.gtmOwner",
  "Product Owner": "role.productOwner",
  "Legal Owner": "role.legalOwner",
  "Finance Owner": "role.financeOwner",
  "Deterministic Control": "role.deterministicControl",
  "Changed-source Owner": "role.changedSourceOwner",
  "Bounded Runtime": "role.boundedRuntime",
  "Canonical Writer": "role.canonicalWriter",
  "Exact Approval Owner": "role.exactApprovalOwner",
  APPROVAL_AUTHORITIES: "publicValidation.adaptation.unknownApprovalAuthorities",
  ORGANIZATION_VALUES: "publicValidation.adaptation.unknownOrganizationValues",
  PERMISSIONS: "publicValidation.adaptation.unknownPermissions",
  REAL_RESPONSIBLE_OWNERS: "publicValidation.adaptation.unknownResponsibleOwners",
  "org:evergreen-industries": "business.organization.evergreen",
  "customer:blue-harbor": "business.customer.blueHarbor",
  "enterprise-quote-operator": "business.role.quoteOperator",
  "human:evergreen-product-owner": "business.role.productOwner",
  "human:evergreen-finance-owner": "business.role.financeOwner",
  "skill:enterprise-quote-compose@1.0": "business.skill.quoteCompose",
  "skill-package:enterprise-quote-compose@1.3.0": "business.skill.quoteCompose",
  "human:skill-steward": "business.role.skillSteward",
  "system:orgrebase-control-plane": "business.role.controlPlane",
  "system:read-only-evidence-view": "business.role.readOnlyEvidence",
  "system:run-scoped-evidence-view": "business.role.readOnlyEvidence",
  "system:pack-admission": "business.role.packAdmission",
  "system:workspace-formation": "business.role.workspaceFormation",
  "system:impact-control": "business.role.impactControl",
  "product-steward": "business.role.productSteward",
  "legal-steward": "business.role.legalSteward",
  "finance-steward": "business.role.financeSteward",
  "gtm-steward": "business.role.gtmSteward",
  "change-coordinator": "business.role.changeCoordinator",
  "quote-reviewer": "business.role.quoteReviewer",
  "independent-reviewer": "business.role.independentReviewer",
  "orgrebase-control-plane": "business.role.controlPlane",
  "domain-candidate": "business.role.domainCandidate",
  "exact-domain-owner": "business.role.exactDomainOwner",
  "@product-steward:controlled.local": "business.actor.productStewardLocal",
  "@legal-steward:controlled.local": "business.actor.legalStewardLocal",
  "@finance-steward:controlled.local": "business.actor.financeStewardLocal",
  "@gtm-steward:controlled.local": "business.actor.gtmStewardLocal",
  ORGREBASE_CONTROL_PLANE: "authority.canonical",
  STATESTORE_REBASE_WORKFLOW_ONLY: "authority.stateStoreOnlyCode",
  "scripted local domain-owner commands": "topology.name.localCommands",
  "Blue Harbor Enterprise Quote": "business.quote.blueHarbor",
  "Evergreen Industries enterprise quote evolution": "business.scenario.evergreen",
  "Evergreen Enterprise Plus": "business.plan.evergreenPlus",
  "US and EU regions supported": "business.residency.usEu",
  "The enterprise launch remains on the approved schedule.": "dataJourney.value.approvedSchedule",
  strategic: "business.priceBand.strategic",
  "legal-review": "business.terms.legalReview",
  "Enterprise Quote": "business.deliverable.enterpriseQuote",
  "Enterprise Quote Finance Approval": "business.impact.financeApproval",
  "Enterprise Launch Readiness Review": "business.impact.launchReadiness",
  "Enterprise Partner Brief": "business.impact.partnerBrief",
  launch_date: "business.change.launchDate",
  currency: "business.change.currency",
  CHANGE: "business.change.generic",
  COMPLETE_COVERAGE_NO_ADMITTED_PATH: "business.reason.coverageNoPath",
  DEPENDENCY_COVERAGE_INSUFFICIENT: "business.reason.insufficientCoverage",
  ADMITTED_TYPED_PATH: "business.reason.admittedPath",
  RESULT_DIGEST_MISMATCH: "business.reason.digestMismatch",
  STALE_ATTEMPT_FENCED: "business.reason.staleAttempt",
  REQUIRES_CLAIM: "business.relation.requiresClaim",
  REQUIRES_POLICY: "business.relation.requiresPolicy",
  SYNTHETIC_GOLD: "business.evidence.syntheticGold",
  OBSERVED_LOCAL_PRODUCT: "business.evidence.localProduct",
  NONE_DETERMINISTIC_MANAGER: "machine.model.deterministicManager",
  NONE_DETERMINISTIC_DOMAIN_WORKER: "machine.model.deterministicWorker",
  LIVE_VERTEX_STRUCTURED_ADVISORY_NOT_DETERMINISTIC_AUTHORITY_NOT_CANONICAL_WRITE: "machine.model.vertexAdvisoryBoundary",
  CONTROLLED_LOCAL_NATIVE_TASKFLOW: "machine.mode.nativeTaskflow",
  CONTROLLED_LOCAL_GOLDEN_COMPETITION: "machine.mode.goldenE2E",
  LOCAL_EXPLICIT_WORKSPACE_APPROVAL: "machine.mode.localApproval",
  DETERMINISTIC_CONTROL_PLANE: "machine.mode.deterministicControl",
  "CONTROLLED_LOCAL GOVERNED SUCCESSOR": "machine.mode.governedSuccessor",
  "NOT EXTERNAL HUMAN VALIDATION": "machine.mode.notExternalValidation",
  "dependency-evidence@CONTROLLED_LOCAL_REAL_HTTP": "machine.tool.dependencyEvidence",
  "StateStore transaction": "machine.tool.stateStoreTransaction",
  "SQLite transaction + event chain": "machine.tool.sqliteEventTransaction",
  "NONE AT WRITE BOUNDARY": "machine.skill.noneAtWrite",
  "OTLP CORRELATED · TRACE ID NOT PROJECTED": "machine.trace.otlpCorrelated",
  "NOT_OBSERVED AT PROJECT NODE": "machine.project.notObserved",
  "CONTROLLED HTTP TOOL": "machine.tool.controlledHttp",
  "enterprise-quote-compose UPSTREAM OF CANDIDATE": "machine.skill.upstreamCandidate",
  "controlled-local@v1": "machine.deployment.controlledLocal",
  "vertex-ai:gemini-3.7-flash advisory + deterministic verifier": "machine.tool.vertex37AdvisoryVerifier",
  "vertex-ai:gemini-3.8-flash advisory + deterministic verifier": "machine.tool.vertex38AdvisoryVerifier",
  CRM_OR_CPQ_REQUEST_QUEUE: "system.crmCpqQueue",
  PRODUCT_CATALOG_OR_RELEASE_SOURCE: "system.productSource",
  CLM_OR_RESTRICTED_LEGAL_SOURCE: "system.legalSource",
  ERP_OR_PRICING_POLICY_SOURCE: "system.financeSource",
  DOCUMENT_OR_CPQ_COMPOSER: "system.quoteComposer",
  CPQ_OR_DOCUMENT_OUTPUT: "system.quoteOutput",
  MANUAL_CROSS_SYSTEM_CHANGE_REVIEW: "system.manualReview",
  MANUAL_DOCUMENT_AND_SYSTEM_REWORK: "system.manualRework",
  CONTROL: "plane.control",
  MINUTE: "unit.minute",
  BUSINESS_HOUR: "unit.businessHour",
  BUSINESS_DAY: "unit.businessDay",
  MODELLED_COUNTERFACTUAL: "value.cost.evidence",
  VALIDATED_PROXY: "enum.validatedProxy",
  MODELLED: "enum.modelled",
  REFERENCE_TEMPLATE: "enum.referenceTemplate",
  DESIGN_TARGET_NOT_OBSERVED_BASELINE: "enum.designTargetBaseline",
  DRAFT: "enum.draft",
  EVALUATED: "enum.evaluated",
  SHADOW: "enum.shadow",
  RELEASE: "enum.release",
  NOT_TRIGGERED: "enum.notTriggered",
  DECISION_ONLY: "enum.decisionOnly",
  REQUALIFICATION_REQUIRED: "enum.requalificationRequired",
  PACKAGE_RESOURCES: "enum.packageResources",
  PRESENT_IN_REPOSITORY: "enum.present",
  PREVIEWED: "event.previewed",
  EVIDENCE_REQUIRED: "event.evidenceRequired",
  APPROVED: "event.approved",
  RECOVERY_REQUIRED: "event.recoveryRequired",
  CURRENT: "event.current",
  RETRY_INTENT: "enum.retryIntent",
  ADOPT_COMMITTED: "enum.adoptCommitted",
  CONTROLLED: "enum.controlled",
  CANONICAL_APPLIED: "enum.canonicalApplied",
  NATIVE_CONTROL_PLANE_EXECUTED: "enum.nativeExecuted",
  DETERMINISTIC_DOMAIN_PROVIDERS: "enum.deterministicProviders",
  "PINNED_TEAMHARNESS + INDEPENDENT_EXECUTOR_PROCESSES": "enum.independentExecutors",
  AWAITING_HUMAN_APPROVAL: "enum.awaitingApproval",
  APPROVED_CANARY: "enum.approvedCanary",
  WAITING_FOR_PENDING_CHANGES: "enum.waitingQuoteV3",
  APPLY_QUOTE: "actionLedger.applyQuote",
  "PERSISTED · REOPENABLE": "enum.persistedReopenable",
  "OrgRebase StateStore and RebaseWorkflow": "authority.canonical",
  "StateStore / RebaseWorkflow": "authority.canonical",
  PRODUCT: "domain.product",
  LEGAL: "domain.legal",
  FINANCE: "domain.finance",
  GTM: "domain.gtm",
  USD: "currency.usd",
  EUR: "currency.eur",
  PACK: "spine.pack",
  SOURCE: "spine.source",
  GOVERNED_APPLY: "spine.governedApply",
  WORKERS: "spine.workers",
  REVIEW: "spine.review",
  FORMATION: "spine.formation",
  REBASE: "spine.rebase",
  QUOTE: "spine.quote",
  EVIDENCE: "spine.evidence",
  PREVIEW: "spine.preview",
  APPLY: "spine.apply",
  TERMINAL: "spine.terminal",
  LIFECYCLE: "spine.lifecycle",
  RETAINED: "spine.retained",
  "synthetic · declared": "spine.detail.synthetic",
  "pinned native control plane": "spine.detail.nativeControl",
  "candidate-only · 0 writes": "spine.detail.candidateWrites",
  "candidate-only · 0 target writes": "spine.detail.candidateWrites",
  "ADVISORY ONLY": "spine.detail.advisory",
  "finance recovery": "spine.detail.financeRecovery",
  "controlled AT receipt": "spine.detail.formationReceipt",
  "explicit click": "spine.detail.explicitClick",
  "StateStore only": "spine.detail.stateStore",
  deliverable: "spine.detail.deliverable",
  "strict admission": "spine.detail.strictAdmission",
  "4-domain context": "spine.detail.domainContext",
  "zero write": "spine.detail.zeroWrite",
  "digest validation failed": "spine.detail.validationFailed",
  "fail closed": "spine.detail.failClosed",
  depends_on: "field.dependsOn",
  "input refs": "field.inputRefs",
  "input digest": "field.inputDigest",
  digest: "field.digest",
  "output digest": "field.outputDigest",
  model: "field.model",
  "model provider": "field.modelProvider",
  "provider request": "field.providerRequest",
  "model authority": "field.modelAuthority",
  tool: "field.tool",
  skill: "field.skill",
  trace: "field.trace",
  "candidate-only": "field.candidateOnly",
  "target writes": "field.targetWrites",
  "evidence / mode": "field.evidenceMode",
});

function displayToken(value) {
  if (value === null || value === undefined || value === "") return "—";
  const raw = String(value);
  const directKey = DISPLAY_KEY_BY_TOKEN[raw];
  if (directKey) return t(directKey);
  let match = raw.match(/^(\d+) ACTIONS?$/i);
  if (match) return t("enum.actions", { count: match[1] });
  match = raw.match(/^(\d+) PROCESSES?$/i);
  if (match) return t("enum.processes", { count: match[1] });
  match = raw.match(/^(\d+) STEPS?$/i);
  if (match) return t("enum.steps", { count: match[1] });
  match = raw.match(/^(\d+) EVENTS?$/i);
  if (match) return t("enum.events", { count: match[1] });
  match = raw.match(/^(\d+) WRITES?$/i);
  if (match) return t("enum.writes", { count: match[1] });
  match = raw.match(/^(\d+) TARGET WRITES?$/i);
  if (match) return t("enum.targetWrites", { count: match[1] });
  match = raw.match(/^attempt (\d+)$/i);
  if (match) return t("enum.attempt", { count: match[1] });
  match = raw.match(/^GOVERNED\s+(.+)$/);
  if (match) return `${t("enum.governed")} ${match[1]}`;
  match = raw.match(/^(.+)\s+CURRENT$/);
  if (match) return `${match[1]} ${t("enum.current")}`;
  if (raw.includes(" · ")) {
    const parts = raw.split(" · ");
    const translated = parts.map((part) => displayToken(part));
    if (translated.some((part, index) => part !== parts[index])) return translated.join(" · ");
  }
  if (raw.includes("→")) {
    const parts = raw.split("→").map((part) => part.trim());
    const translated = parts.map((part) => displayToken(part));
    if (translated.some((part, index) => part !== parts[index])) return translated.join(" → ");
  }
  return raw;
}

function localizedFactHtml(value, { showRaw = false } = {}) {
  const raw = value === null || value === undefined || value === "" ? "—" : String(value);
  const localized = displayToken(raw);
  if (!showRaw || localized === raw || raw === "—") return escapeHtml(localized);
  return `<span class="localized-fact"><span class="localized-primary">${escapeHtml(localized)}</span><span class="machine-token">${escapeHtml(raw)}</span></span>`;
}

function localizedText(id, value, options = {}) {
  byId(id).innerHTML = localizedFactHtml(value, options);
}

function localizedBusinessText(id, value) {
  const node = byId(id);
  const raw = value === null || value === undefined || value === "" ? "—" : String(value);
  const localized = displayToken(raw);
  node.textContent = localized;
  node.removeAttribute("title");
}

function storedLanguage() {
  try {
    const value = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return SUPPORTED_LANGUAGES.has(value) ? value : DEFAULT_LANGUAGE;
  } catch (_) {
    return DEFAULT_LANGUAGE;
  }
}

function applyStaticTranslations() {
  document.title = t("meta.title");
  document.documentElement.lang = currentLanguage;
  document.documentElement.dataset.language = currentLanguage;
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((node) => {
    node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
  });
  document.querySelectorAll(".language-option").forEach((button) => {
    const active = button.dataset.language === currentLanguage;
    const label = button.dataset.language === "zh-CN" ? t("language.switchChinese") : t("language.switchEnglish");
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.setAttribute("aria-label", label);
    button.title = label;
  });
}

const STAGES = ["EMPTY", "CURRENT", "PREVIEWED", "APPROVED", "RECOVERY_REQUIRED"];

const UNAVAILABLE_WORKSPACE_PROJECTION = Object.freeze({
  stage: "UNAVAILABLE",
  event_chain: Object.freeze({ status: "UNAVAILABLE", events: 0 }),
  event_scopes: Object.freeze({}),
});

const STAGE_ACTIONS = {
  EMPTY: { labelKey: "action.empty.label", titleKey: "action.empty.title", detailKey: "action.empty.detail", roleKey: "action.empty.role", method: "open-task-intake" },
  preview: { labelKey: "event.preview", titleKey: "event.preview", detailKey: "event.previewDetail", roleKey: "action.control.role", method: "preview" },
  approve: { labelKey: "event.approve", titleKey: "event.approve", detailKey: "event.approveDetail", roleKey: "action.owner.role", method: "approve" },
  apply: { labelKey: "event.apply", titleKey: "event.apply", detailKey: "event.applyDetail", roleKey: "action.control.role", method: "apply" },
  recover: { labelKey: "event.recover", titleKey: "event.recover", detailKey: "event.recoverDetail", roleKey: "action.control.role", method: "apply" },
  idle: { labelKey: "event.idle", titleKey: "event.idle", detailKey: "event.idleDetail", roleKey: "action.complete.role", method: null },
};

const primaryButton = byId("primary-action");
const quoteDownloadButton = byId("download-quote");
const evidenceDownloadButton = byId("download-evidence");
const HUMAN_REVIEW_HOLD_MS = 4000;
let currentState = null;
let workspaceStateAvailability = "loading";
let inFlight = false;
let toastTimer = null;
let reviewHoldKey = null;
let reviewReadyAt = 0;
let reviewTimer = null;
let serverReviewWait = null;
let experienceInFlight = false;
let experienceTimer = null;
let currentSkillCatalog = null;
let selectedSkillName = null;
let skillDraftEditing = false;
let skillDraftInFlight = false;
let skillSourceRevision = 0;
let skillDialogStatus = { key: "skill.source.readonlyStatus", params: {}, tone: "neutral" };
let currentHealth = null;
let currentReadiness = null;
let currentRetainedEvidence = null;
let currentOperatingModel = null;
let currentPublicRealProcessValidation = null;
let currentReleaseFacts = null;
let currentOacAdaptationProof = null;
let currentRunArchive = null;
let currentRunArchiveRequestedFor = null;
let currentRunObservability = null;
let currentRunObservabilityRequestedFor = null;
let oacWorkspaceGateObserved = false;
let currentOacWorkspaceGate = {
  valid: false,
  mode: "unknown",
  requiresOacAdmission: true,
  formAllowed: false,
  status: "BLOCKED_PENDING_OAC",
  executionRunId: null,
  activationBindingDigest: null,
  consumptionReceiptDigest: null,
};

function normalizeOacWorkspaceGate(payload) {
  const source = payload && (payload.workspaceGate || payload.workspace_gate || payload);
  const closed = {
    valid: false,
    mode: "unknown",
    requiresOacAdmission: true,
    formAllowed: false,
    status: "BLOCKED_PENDING_OAC",
    executionRunId: source && (source.executionRunId || source.execution_run_id) || null,
    activationBindingDigest: source && (source.activationBindingDigest || source.activation_binding_digest) || null,
    consumptionReceiptDigest: source && (source.consumptionReceiptDigest || source.consumption_receipt_digest) || null,
  };
  if (!source || typeof source !== "object" || Array.isArray(source)) return closed;
  const mode = String(source.mode || "").trim().toLowerCase();
  const status = String(source.status || "").trim().toUpperCase();
  const requiresOacAdmission = source.requiresOacAdmission ?? source.requires_oac_admission;
  const formAllowed = source.formAllowed ?? source.form_allowed;
  const executionRunId = source.executionRunId || source.execution_run_id;
  const activationBindingDigest = source.activationBindingDigest || source.activation_binding_digest;
  const consumptionReceiptDigest = source.consumptionReceiptDigest || source.consumption_receipt_digest;
  const statuses = new Set([
    "BLOCKED_PENDING_OAC",
    "READY_TO_FORM",
    "CONSUMED_BY_QUOTE_FORMATION",
  ]);
  const commonValid = ["off", "required", "optional"].includes(mode)
    && statuses.has(status)
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
  const optionalValid = !["off", "optional"].includes(mode) || (
    requiresOacAdmission === false
    && formAllowed === true
    && status !== "BLOCKED_PENDING_OAC"
  );
  if (!commonValid || !requiredValid || !optionalValid) return closed;
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

function openDetailPositions() {
  return Array.from(document.querySelectorAll("details[open]")).map((node) => {
    const siblings = Array.from(document.querySelectorAll("details"));
    return siblings.indexOf(node);
  });
}

function restoreOpenDetails(positions) {
  const details = Array.from(document.querySelectorAll("details"));
  positions.forEach((position) => {
    if (details[position]) details[position].open = true;
  });
}

function setLanguage(language, { persist = true } = {}) {
  const nextLanguage = SUPPORTED_LANGUAGES.has(language) ? language : DEFAULT_LANGUAGE;
  const openDetails = openDetailPositions();
  currentLanguage = nextLanguage;
  if (persist) {
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
    } catch (_) {
      // Private or restricted browsing may reject storage; language still works in-memory.
    }
  }
  applyStaticTranslations();
  if (currentState) render(currentState);
  else if (workspaceStateAvailability === "unavailable") renderWorkspaceUnavailable({ notify: false });
  if (currentRetainedEvidence) renderSemifinalEvidence(currentRetainedEvidence);
  else if (currentState || currentHealth || currentReadiness) renderOperations(currentState || UNAVAILABLE_WORKSPACE_PROJECTION);
  if (currentPublicRealProcessValidation) renderPublicRealProcessValidation(currentPublicRealProcessValidation);
  renderCurrentProofOverview();
  renderSkillSourceCatalog();
  renderSkillDialogDynamicCopy();
  restoreOpenDetails(openDetails);
  window.dispatchEvent(new CustomEvent("orgrebase:languagechange", {
    detail: { language: currentLanguage },
  }));
}

function stageIndex(stage) {
  const index = STAGES.indexOf(stage);
  return index === -1 ? 0 : index;
}

function text(id, value) {
  byId(id).textContent = value === null || value === undefined || value === "" ? "—" : String(value);
}

function exactAuditTitle(id, ...values) {
  const node = byId(id);
  const exact = values.filter((value) => value !== null && value !== undefined && value !== "").join(" · ");
  if (exact) node.title = exact;
  else node.removeAttribute("title");
}

function shortDigest(value, width = 22) {
  if (!value) return "—";
  const stringValue = String(value);
  return stringValue.length > width ? `${stringValue.slice(0, width)}…` : stringValue;
}

function isSha256Digest(value) {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message, tone = "default") {
  const node = byId("toast");
  node.textContent = message;
  node.dataset.tone = tone;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3000);
}

function errorMessage(payload, response) {
  const detail = payload && payload.detail;
  let message;
  if (detail && typeof detail === "object" && detail.code) {
    const translations = {
      APPROVAL_TIME_WINDOW_INVALID: "error.approvalWindow",
      APPROVAL_TIMESTAMP_INVALID: "error.approvalTimestamp",
    };
    const reason = Object.hasOwn(translations, detail.code) ? detail.code
      : detail.code === "EVIDENCE_INTEGRITY_FAILED" && Object.hasOwn(translations, detail.message)
        ? detail.message : null;
    message = reason ? t(translations[reason]) : t("error.apiCode", { code: detail.code });
  } else if (detail && (typeof detail === "object" || typeof detail === "string")) {
    message = t("error.request", { status: response.status });
  } else {
    message = (payload && payload.message) || t("error.request", { status: response.status });
  }
  const incident = detail && typeof detail === "object" && detail.incident_id;
  return typeof incident === "string" && /^[0-9a-f]{32}$/.test(incident)
    ? `${message} · ${t("error.incident", { id: incident })}` : message;
}

async function api(path, options = {}) {
  try {
    // History is paged separately; the server also retains the active change.
    const endpoint = path === "/api/workspace/state" ? `${path}?history_limit=5` : path;
    return await window.OrgRebaseClient.json(endpoint, options);
  } catch (error) {
    if ("payload" in error) {
      error.message = errorMessage(error.payload, { status: error.status });
      const detail = error.detail;
      if (detail && typeof detail === "object") {
        error.remainingMs = detail.remaining_ms;
        error.notBefore = detail.not_before;
      }
      error.httpStatus = error.status;
    }
    throw error;
  }
}

function activePreview(state) {
  return state.active_event_id && state.changes && state.changes[state.active_event_id]
    ? state.changes[state.active_event_id].preview : state.latest_preview || null;
}

function previewBundle(state) {
  const latest = activePreview(state);
  return (latest && (latest.bundle || latest)) || null;
}

const VMRC_DISPOSITION_BY_CLASSIFICATION = Object.freeze({
  AFFECTED_HARD: "REBUILD",
  AFFECTED_REVIEW: "HOLD_FOR_REVIEW",
  AFFECTED_INFORMATIONAL: "HOLD_FOR_REVIEW",
  UNAFFECTED_WITHIN_DECLARED_BOUNDARY: "PRESERVE_WITHIN_BOUNDARY",
  UNKNOWN: "HOLD_FOR_REVIEW",
  REQUALIFICATION_REQUIRED: "REQUALIFY",
});

function exactVmrcBinding(bundle) {
  if (!bundle || typeof bundle !== "object") return null;
  const changeSet = bundle.change_set;
  const preview = bundle.preview || bundle.impact_preview;
  const certificate = bundle.minimal_rebase_certificate || bundle.certificate;
  if (!changeSet || !preview || !certificate) return null;
  if (
    certificate.schema_version !== "orgrebase.minimal-rebase-certificate.v1"
      || certificate.verifier_version !== "orgrebase.minimal-rebase-verifier@1.0.0"
      || !isSha256Digest(certificate.digest)
      || !isSha256Digest(certificate.impact_certificate_set_digest)
      || !isSha256Digest(changeSet.digest)
      || !isSha256Digest(preview.digest)
      || !preview.revision_lock
      || !isSha256Digest(preview.revision_lock.digest)
      || certificate.change_set_digest !== changeSet.digest
      || certificate.preview_digest !== preview.digest
      || certificate.revision_lock_digest !== preview.revision_lock.digest
  ) return null;

  const results = Array.isArray(preview.results) ? preview.results : [];
  const effects = Array.isArray(certificate.effects) ? certificate.effects : [];
  if (!results.length || results.length !== effects.length) return null;
  const effectsByTarget = new Map();
  for (const effect of effects) {
    if (
      !effect
        || typeof effect.target_id !== "string"
        || effectsByTarget.has(effect.target_id)
        || !isSha256Digest(effect.impact_result_digest)
        || !isSha256Digest(effect.impact_certificate_digest)
    ) return null;
    effectsByTarget.set(effect.target_id, effect);
  }
  const resultTargets = new Set();
  for (const result of results) {
    const expectedDisposition = result && VMRC_DISPOSITION_BY_CLASSIFICATION[result.classification];
    if (
      !result
        || typeof result.object_id !== "string"
        || resultTargets.has(result.object_id)
        || !isSha256Digest(result.digest)
        || !expectedDisposition
    ) return null;
    resultTargets.add(result.object_id);
    const effect = effectsByTarget.get(result.object_id);
    if (
      !effect
        || effect.impact_result_digest !== result.digest
        || effect.disposition !== expectedDisposition
    ) return null;
  }
  return resultTargets.size === effectsByTarget.size ? certificate : null;
}

const PREVIEW_KIND_OBJECT_IDS = Object.freeze({
  launch_date: "claim:product.launch_date",
  currency: "policy:finance.currency",
});

function previewDeltaForKind(state, kind) {
  const latest = activePreview(state);
  const expectedObjectId = PREVIEW_KIND_OBJECT_IDS[kind];
  if (!latest || latest.kind !== kind || !expectedObjectId) return null;
  const bundle = previewBundle(state);
  const objectId = bundle && bundle.change_spec && bundle.change_spec.object_id;
  const deltas = bundle && bundle.change_set && Array.isArray(bundle.change_set.deltas)
    ? bundle.change_set.deltas
    : [];
  if (objectId !== expectedObjectId) return null;
  return deltas.find((delta) => delta && delta.object_id === expectedObjectId) || null;
}

function oacGateActionForState(state) {
  if (!state || state.stage !== "EMPTY") return null;
  if (!oacWorkspaceGateObserved || !currentOacWorkspaceGate.valid) {
    return {
      label: t("action.oacChecking.label"),
      title: t("action.oacChecking.title"),
      detail: t("action.oacChecking.detail"),
      actor: t("action.oacChecking.actor"),
      role: t("action.oacChecking.role"),
      method: null,
    };
  }
  const gate = currentOacWorkspaceGate;
  const blocked = !gate.valid || (gate.requiresOacAdmission && !gate.formAllowed);
  if (!blocked) return null;
  return {
    label: t("action.oacGate.label"),
    title: t("action.oacGate.title"),
    detail: t("action.oacGate.detail"),
    actor: t("action.oacGate.actor"),
    role: t("action.oacGate.role"),
    method: "open-oac",
  };
}

function activeApproval(state) {
  return state.active_event_id && state.changes && state.changes[state.active_event_id]
    ? state.changes[state.active_event_id].approval : state.latest_approval || null;
}

function experienceReviewActionForState(state) {
  const experience = state && state.experience_governance;
  if (!state || !state.business_complete || !experience || experience.status !== "AWAITING_HUMAN_APPROVAL") {
    return null;
  }
  return {
    label: t("action.experienceReview.label"),
    title: t("action.experienceReview.title"),
    detail: t("action.experienceReview.detail"),
    actor: experience.owner_id || t("action.experienceReview.actor"),
    role: t("action.experienceReview.role"),
    method: "open-experience",
  };
}

function actionForState(state) {
  const oacGateAction = oacGateActionForState(state);
  if (oacGateAction) return oacGateAction;
  const controls = state.actions || {};
  const config = STAGE_ACTIONS[state.stage === "EMPTY" ? "EMPTY"
    : state.stage === "RECOVERY_REQUIRED" && controls.next_operation === "apply" ? "recover"
      : controls.next_operation || "idle"];
  const eventId = controls.next_event_id || null;
  const scenario = state.scenario || {};
  const delta = eventId ? previewDeltaForKind(state, eventId) : null;
  return {
    ...config,
    method: eventId ? "open-change" : config.method,
    kind: eventId,
    actor: config.method === "approve" ? controls.owner_id : config.method === "preview" ? "system:orgrebase-impact-engine" : "system:orgrebase-control-plane",
    label: eventId ? t("action.openChange.label") : state.stage === "EMPTY" && scenario.label ? t("action.dynamic.start", { label: displayToken(scenario.label) }) : t(config.labelKey),
    title: delta ? `${displayToken(delta.object_id)} · ${displayToken(delta.base_value)} → ${displayToken(delta.proposed_value)}` : t(config.titleKey),
    detail: t(config.detailKey),
    role: t(config.roleKey),
  };
}

function reviewIdentity(state) {
  const preview = activePreview(state);
  const gate = preview && preview.review_gate && preview.review_gate.gate;
  return `${state.stage}:${(preview && preview.preview_digest) || "missing-preview"}:${(gate && gate.digest) || "missing-gate"}`;
}

function clearReviewHold() {
  clearTimeout(reviewTimer);
  reviewTimer = null;
  reviewHoldKey = null;
  reviewReadyAt = 0;
  serverReviewWait = null;
}

function reviewGateReadyAt(state) {
  const preview = activePreview(state);
  const gate = preview && preview.review_gate && preview.review_gate.gate;
  if (gate && Number.isFinite(Number(gate.not_before_epoch_ms))) return Number(gate.not_before_epoch_ms);
  if (gate && gate.not_before) {
    const parsed = Date.parse(gate.not_before);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function reviewHoldRemaining(state, action) {
  if (action.method !== "approve") {
    clearReviewHold();
    return 0;
  }
  const key = reviewIdentity(state);
  const serverReadyAt = reviewGateReadyAt(state);
  if (serverReadyAt !== null) {
    reviewHoldKey = key;
    reviewReadyAt = serverReadyAt;
  } else if (reviewHoldKey !== key) {
    reviewHoldKey = key;
    reviewReadyAt = Date.now() + HUMAN_REVIEW_HOLD_MS;
  }
  if (serverReviewWait && serverReviewWait.key === key) {
    reviewReadyAt = Math.max(reviewReadyAt, serverReviewWait.readyAt);
  }
  return Math.max(0, reviewReadyAt - Date.now());
}

function scheduleReviewRender(remaining) {
  clearTimeout(reviewTimer);
  reviewTimer = null;
  if (remaining <= 0) return;
  reviewTimer = setTimeout(() => {
    if (currentState) renderCommand(currentState);
  }, Math.min(1000, remaining + 20));
}

function currentRunId(state) {
  const runId = state && state.execution && state.execution.run_id;
  return typeof runId === "string" && runId && runId !== "UNAVAILABLE" ? runId : null;
}

function sameRunActivityRow(row, runId) {
  return Boolean(
    row
      && runId
      && row.run_id === runId
      && row.source_run_id === runId,
  );
}

function exactTaskInvocation(row, task, runId, expected) {
  if (!sameRunActivityRow(row, runId) || !task || !expected) return false;
  return task.agent_name === row.actor_or_domain
    && task.role === expected.role
    && String(task.authority_domain || "").toLowerCase() === expected.domain
    && Number(task.attempt) === expected.attempt
    && task.candidate_only === true
    && Number(task.target_writes) === 0
    && row.plane === expected.plane
    && row.action === expected.action
    && row.tool_or_skill === expected.toolOrSkill
    && row.source_ref === expected.sourceRef
    && row.status === expected.status
    && row.permission === "CANDIDATE_ONLY"
    && Number(row.target_writes) === 0
    && Boolean(expected.receiptDigest)
    && row.receipt_digest === expected.receiptDigest;
}

function exactFinanceToolInvocation(rows, runId, tool, task) {
  if (!Array.isArray(rows)
      || !tool
      || tool.status !== "SUCCEEDED"
      || Number(tool.target_writes) !== 0
      || !tool.operation
      || !tool.receipt_digest) return null;
  return rows.find((row) => exactTaskInvocation(row, task, runId, {
    role: "DOMAIN_WORKER",
    domain: "finance",
    attempt: 2,
    plane: "TOOL",
    action: "RECOVER_FINANCE_DEPENDENCY",
    toolOrSkill: tool.operation,
    sourceRef: tool.operation,
    status: "SUCCEEDED",
    receiptDigest: tool.receipt_digest,
  })) || null;
}

function exactGtmSkillInvocation(rows, runId, skill, task) {
  if (!Array.isArray(rows)
      || !skill
      || skill.status !== "SUCCESS"
      || Number(skill.target_writes) !== 0
      || !skill.package_id
      || !skill.receipt_digest) return null;
  return rows.find((row) => exactTaskInvocation(row, task, runId, {
    role: "DOMAIN_WORKER",
    domain: "gtm",
    attempt: 1,
    plane: "SKILL",
    action: "COMPOSE_REVIEWED_QUOTE_CANDIDATE",
    toolOrSkill: skill.package_id,
    sourceRef: skill.package_id,
    status: "SUCCESS",
    receiptDigest: skill.receipt_digest,
  })) || null;
}

function activeCompetition(state) {
  const evidence = state && state.competition_evidence;
  const runId = currentRunId(state);
  return evidence
    && evidence.status === "PASS"
    && evidence.run_id === runId
    ? evidence
    : null;
}

function stateExecution(state) {
  const base = state.execution || {
    run_id: "UNAVAILABLE",
    mode: (state.boundaries && state.boundaries.execution_profile) || "LOCAL_DETERMINISTIC",
    candidate_runtime: "DETERMINISTIC_DOMAIN_PROVIDERS",
    canonical_authority: "OrgRebase StateStore and RebaseWorkflow",
    agentteams: (state.boundaries && state.boundaries.agentteams) || "NOT_RUN",
    oac_admission: (state.boundaries && state.boundaries.oac_runtime_bridge) || "NOT_USED_IN_THIS_RUN",
  };
  const competition = activeCompetition(state);
  return competition
    ? {
        ...base,
        run_id: base.run_id,
        correlation_id: competition.correlation_id,
        mode: "CONTROLLED_LOCAL_AGENTTEAMS",
        candidate_runtime: "PINNED_TEAMHARNESS + INDEPENDENT_EXECUTOR_PROCESSES",
        agentteams: "NATIVE_CONTROL_PLANE_EXECUTED",
      }
    : base;
}

function activePilotActions(state) {
  const projection = state.execution_activity || {};
  const execution = stateExecution(state);
  if (!Array.isArray(projection.rows) || !execution.run_id || execution.run_id === "UNAVAILABLE") return [];
  return projection.rows
    .filter((row) => sameRunActivityRow(row, execution.run_id) && row.receipt_digest)
    .map((row) => {
      let type = "AUTO";
      if (row.permission === "CANDIDATE_ONLY") type = "CANDIDATE";
      else if (row.permission === "APPROVAL") type = "HUMAN";
      else if (row.permission === "CANONICAL_WRITE") type = "WRITE";
      else if (row.plane === "TOOL") type = "TOOL";
      return {
        sequence: row.sequence,
        type,
        actor: row.actor_or_domain,
        action: row.tool_or_skill ? `${row.action} · ${row.tool_or_skill}` : row.action,
        status: row.status,
        writes: row.permission === "CANONICAL_WRITE" ? "GOVERNED" : (row.target_writes ?? 0),
        digest: row.receipt_digest,
      };
    });
}

function typePill(type) {
  const normalized = String(type || "AUTO").toLowerCase();
  return `<span class="type-pill ${escapeHtml(normalized)}">${localizedFactHtml(type || "AUTO")}</span>`;
}

function renderActiveActionLedger(state) {
  const rows = activePilotActions(state);
  text("active-ledger-count", t("scene.ledger.count", { count: rows.length }));
  byId("active-action-ledger").innerHTML = rows.map((row) => `<tr>
    <td>${row.sequence}</td>
    <td>${typePill(row.type)}</td>
    <td title="${escapeHtml(row.actor)}">${escapeHtml(displayToken(row.actor))}</td>
    <td title="${escapeHtml(row.action)}">${localizedFactHtml(row.action)}</td>
    <td>${localizedFactHtml(row.status)}</td>
    <td>${localizedFactHtml(row.writes)}</td>
    <td title="${escapeHtml(row.digest || "—")}">${escapeHtml(shortDigest(row.digest, 16))}</td>
  </tr>`).join("");
}

function spineNode(label, status, detail, tone = "pass") {
  return `<div class="spine-node ${escapeHtml(tone)}"><span>${localizedFactHtml(label, { showRaw: false })}</span><strong>${localizedFactHtml(status)}</strong><small>${localizedFactHtml(detail || "—")}</small></div>`;
}

function renderActiveEvidenceSpine(state) {
  const source = state.enterprise_data_lineage && state.enterprise_data_lineage.source || {};
  const profile = state.enterprise_seed_profile || {};
  const scenario = state.scenario || {};
  const packAdmitted = source.status === "ADMITTED_AND_MATCHED"
    && Boolean(profile.profile_ref)
    && Boolean(scenario.pack_digest);
  const packNode = () => spineNode(
    "PACK",
    packAdmitted ? "PASS" : "WAITING",
    packAdmitted ? `${profile.data_class || "DECLARED"} · ${shortDigest(scenario.pack_digest, 11)}` : "admission evidence not observed",
    packAdmitted ? "pass" : "not-run",
  );
  const competition = activeCompetition(state);
  if (competition) {
    const collaboration = competition.agent_collaboration || {};
    const reviewer = collaboration.reviewer || {};
    const tool = collaboration.tool || {};
    const skill = collaboration.skill || {};
    const tasks = Array.isArray(collaboration.orchestration_plan && collaboration.orchestration_plan.tasks)
      ? collaboration.orchestration_plan.tasks
      : [];
    const activityRows = Array.isArray(state.execution_activity && state.execution_activity.rows)
      ? state.execution_activity.rows
      : [];
    const financeAttemptTwo = tasks.find((task) => (
      task.role === "DOMAIN_WORKER" && task.authority_domain === "finance" && task.attempt === 2
    )) || null;
    const gtmComposeTask = tasks.find((task) => (
      task.role === "DOMAIN_WORKER" && task.authority_domain === "gtm" && task.attempt === 1
    )) || null;
    const toolActivity = exactFinanceToolInvocation(activityRows, competition.run_id, tool, financeAttemptTwo);
    const skillActivity = exactGtmSkillInvocation(activityRows, competition.run_id, skill, gtmComposeTask);
    const approval = activeApproval(state);
    const outcome = state.latest_outcome || null;
    const quote = state.quote || {};
    const chain = projectedEventScopes(state).quote;
    byId("active-evidence-spine").innerHTML = [
      packNode(),
      spineNode("AGENTTEAMS", `${competition.agentteams_action_count || 0} ACTIONS`, "pinned native control plane"),
      spineNode("WORKERS", `${competition.independent_domain_worker_processes || 0} PROCESSES`, "candidate-only · 0 writes"),
      spineNode(
        "REVIEW",
        `${reviewer.attempt_1 && reviewer.attempt_1.verdict || "—"} → ${reviewer.attempt_2 && reviewer.attempt_2.verdict || "—"}`,
        `${reviewer.model_provider || "MODEL"} · ${reviewer.model_version || reviewer.model_evidence_class || "NOT_OBSERVED"} · ADVISORY ONLY`,
      ),
      spineNode("TOOL", toolActivity ? toolActivity.status : "WAITING", toolActivity ? toolActivity.tool_or_skill : "exact Finance attempt 2 binding not observed", toolActivity ? "pass" : "not-run"),
      spineNode("SKILL", skillActivity ? skill.action : "WAITING", skillActivity ? skillActivity.tool_or_skill : "exact GTM compose binding not observed", skillActivity ? "pass" : "not-run"),
      spineNode("FORMATION", state.formation ? "COMMITTED" : "VERIFIED", "controlled AT receipt", "pass"),
      spineNode("HUMAN", approval ? "APPROVED" : "WAITING", approval ? approvalActor(approval) : "explicit click", approval ? "human" : "not-run"),
      spineNode("REBASE", outcome ? "GOVERNED" : "WAITING", "StateStore only", outcome ? "pass" : "not-run"),
      spineNode("QUOTE", quote.version ? quoteRevisionLabel(quote.version) : "NOT FORMED", quote.id || "deliverable", quote.version ? "pass" : "not-run"),
      spineNode("EVIDENCE", chain.status || "WAITING", `${eventCount(chain)} events`, chain.status === "PASS" ? "pass" : "not-run"),
    ].join("");
    return;
  }
  const tool = state.dependency_evidence_tool || {};
  const preview = activePreview(state);
  const approval = activeApproval(state);
  const outcome = state.latest_outcome || null;
  const quote = state.quote || {};
  const chain = projectedEventScopes(state).quote;
  byId("active-evidence-spine").innerHTML = [
    packNode(),
    spineNode("FORMATION", state.formation ? "COMPLETED" : "WAITING", "4-domain context", state.formation ? "pass" : "not-run"),
    spineNode("TOOL", tool.status || "NOT_RUN", `${tool.target_writes ?? 0} target writes`, tool.status === "SUCCEEDED" ? "pass" : "not-run"),
    spineNode("PREVIEW", preview ? "LOCKED" : "WAITING", preview ? shortDigest(preview.preview_digest, 13) : "zero write", preview ? "pass" : "not-run"),
    spineNode("HUMAN", approval ? "APPROVED" : "WAITING", approval ? approvalActor(approval) : "explicit click", approval ? "human" : "not-run"),
    spineNode("APPLY", outcome ? "GOVERNED" : "WAITING", "StateStore only", outcome ? "pass" : "not-run"),
    spineNode("QUOTE", quote.version ? quoteRevisionLabel(quote.version) : "NOT FORMED", quote.id || "deliverable", quote.version ? "pass" : "not-run"),
    spineNode("EVIDENCE", chain.status || "WAITING", `${eventCount(chain)} events`, chain.status === "PASS" ? "pass" : "not-run"),
  ].join("");
}

function activeAdvisorySnapshot(state) {
  const competition = activeCompetition(state) || {};
  const future = competition.agent_collaboration || competition.collaboration;
  if (future && (future.orchestration_plan || future.agent_runs || future.handoffs)) return future;
  const advisories = Object.keys(state.changes || {})
    .map((kind) => state.changes && state.changes[kind] && state.changes[kind].preview)
    .filter(Boolean)
    .map((preview) => {
      const bundle = preview.bundle || preview;
      return bundle && bundle.advisory;
    })
    .filter(Boolean);
  if (!advisories.length) {
    const preview = activePreview(state);
    const bundle = preview && (preview.bundle || preview);
    if (bundle && bundle.advisory) advisories.push(bundle.advisory);
  }
  if (!advisories.length) return null;
  const plans = advisories.map((advisory) => advisory.orchestration_plan || advisory.plan || {});
  return {
    orchestration_plan: {
      digest: plans.map((plan) => plan.digest).filter(Boolean),
      tasks: plans.flatMap((plan) => Array.isArray(plan.tasks) ? plan.tasks : []),
    },
    agent_runs: advisories.flatMap((advisory) => Array.isArray(advisory.agent_runs) ? advisory.agent_runs : []),
    handoffs: advisories.flatMap((advisory) => Array.isArray(advisory.handoffs) ? advisory.handoffs : []),
  };
}

const DOMAIN_WORKERS = [
  { domain: "product", label: "PRODUCT", actor: "product-steward" },
  { domain: "legal", label: "LEGAL", actor: "legal-steward" },
  { domain: "finance", label: "FINANCE", actor: "finance-steward" },
  { domain: "gtm", label: "GTM", actor: "gtm-steward" },
];

function observed(value) {
  if (value === null || value === undefined || value === "") return "NOT_OBSERVED";
  if (value === true) return "TRUE";
  if (value === false) return "FALSE";
  if (Array.isArray(value)) return value.length ? value.join(" · ") : "NONE";
  return String(value);
}

function nodeField(label, value, digest = false) {
  const full = observed(value);
  const opaqueDigest = digest && !["NOT_OBSERVED", "NOT_APPLICABLE", "NONE", "N/A"].includes(full);
  const rendered = opaqueDigest ? shortDigest(full, 27) : full;
  const localizedLabel = displayToken(label);
  return `<div><dt title="${escapeHtml(label)}">${escapeHtml(localizedLabel)}</dt><dd title="${escapeHtml(full)}">${opaqueDigest ? escapeHtml(rendered) : localizedFactHtml(rendered)}</dd></div>`;
}

function presentTopologyDetail(value) {
  const normalized = Array.isArray(value)
    ? value.filter((item) => item !== null && item !== undefined && String(item).trim()).join(" · ")
    : value;
  if (normalized === null || normalized === undefined || String(normalized).trim() === "") return null;
  const machineToken = String(normalized).trim().toUpperCase();
  if (["NONE", "N/A", "NOT_APPLICABLE", "NOT_OBSERVED"].includes(machineToken)) return null;
  return normalized;
}

function topologySummaryField(key, value) {
  const present = presentTopologyDetail(value);
  return present === null ? "" : nodeField(t(key), present);
}

function topologyNode(node) {
  const tone = node.tone || "not-run";
  const upstreamCount = Array.isArray(node.dependsOn)
    ? node.dependsOn.filter(Boolean).length
    : node.dependsOn ? 1 : 0;
  const inputCount = Array.isArray(node.inputRefs)
    ? node.inputRefs.filter(Boolean).length
    : node.inputRefs || node.inputDigest ? 1 : 0;
  const canonicalWrite = tone === "store" && Boolean(node.writes);
  const candidateOutput = node.candidateOnly === true;
  const outputNature = canonicalWrite
    ? t("topology.detail.canonicalOutput")
    : candidateOutput
      ? t("topology.detail.candidateOutput")
      : t("topology.detail.controlOutput");
  const authorityBoundary = canonicalWrite
    ? t("topology.detail.canonicalAuthority")
    : candidateOutput
      ? t("topology.detail.candidateAuthority")
      : t("topology.detail.controlAuthority");
  const executionMode = node.executionMode || (
    tone === "store" ? t("topology.detail.storeExecution")
      : tone === "human" ? t("topology.detail.humanExecution")
        : node.actorKind === "oac-compiler" ? t("topology.detail.oacCompiler")
          : node.actorKind === "agent" ? t("topology.detail.agentExecution")
            : t("topology.detail.controlExecution")
  );
  const invocationAudit = node.invocation && node.invocation.receipt
    ? `<details class="topology-technical-audit">
        <summary>${escapeHtml(t("topology.detail.technicalAudit"))}<b aria-hidden="true">＋</b></summary>
        <dl>${nodeField(t("topology.detail.invocationReceipt"), node.invocation.receipt, true)}</dl>
      </details>`
    : "";
  return `<details class="topology-node ${escapeHtml(tone)}">
    <summary>
      <span>${escapeHtml(node.role)}</span>
      <strong>${localizedFactHtml(node.name)}</strong>
      <small>${localizedFactHtml(observed(node.status))}</small>
      <b aria-hidden="true">+</b>
    </summary>
    <dl>
      ${topologySummaryField("topology.detail.task", node.taskSummary)}
      ${topologySummaryField("topology.detail.context", node.contextSummary)}
      ${topologySummaryField("topology.detail.outputSummary", node.outputSummary)}
      ${topologySummaryField("topology.detail.handoff", node.handoffTo)}
      ${topologySummaryField("topology.detail.reviewDecision", node.reviewDecision)}
      ${topologySummaryField("topology.detail.tool", node.tool)}
      ${topologySummaryField("topology.detail.skill", node.skill)}
      ${topologySummaryField("topology.detail.effect", node.effectSummary)}
      ${nodeField(t("topology.detail.upstream"), upstreamCount
        ? t("topology.detail.upstreamCount", { count: upstreamCount })
        : t("topology.detail.noUpstream"))}
      ${nodeField(t("topology.detail.input"), t("topology.detail.inputCount", { count: inputCount }))}
      ${nodeField(t("topology.detail.output"), outputNature)}
      ${nodeField(t("topology.detail.authority"), authorityBoundary)}
      ${nodeField(t("topology.detail.execution"), executionMode)}
      ${node.invocation ? nodeField(
        t("topology.detail.invocation"),
        `${node.invocation.kind} · ${node.invocation.name} · ${observed(node.invocation.status)}`,
      ) : ""}
    </dl>
    ${invocationAudit}
  </details>`;
}

function topologyStage(label, nodes, className) {
  return `<section class="topology-stage ${escapeHtml(className)}">
    <h3>${escapeHtml(label)}</h3>
    <div class="topology-stage-nodes">${nodes.map(topologyNode).join("")}</div>
  </section>`;
}

function renderDirectedTopology(containerId, stages) {
  byId(containerId).innerHTML = stages.map((stage, index) => `${topologyStage(stage.label, stage.nodes, stage.className)}${
    index < stages.length - 1 ? '<span class="topology-arrow" aria-hidden="true">→</span>' : ""
  }`).join("");
}

function taskForAgent(tasks, agentName, domain) {
  const reversed = [...tasks].reverse();
  return reversed.find((task) => task.agent_name === agentName)
    || reversed.find((task) => String(task.authority_domain || "").toLowerCase() === domain)
    || null;
}

function runForTask(runs, task, agentName) {
  return (task && runs.find((run) => run.task_id === task.id))
    || runs.find((run) => run.agent_name === agentName)
    || null;
}

function handoffForTask(handoffs, task, agentName) {
  return (task && handoffs.find((handoff) => handoff.task_id === task.id))
    || handoffs.find((handoff) => handoff.from_agent === agentName)
    || null;
}

function goldenTaskNode(task, runs, handoffs, overrides = {}) {
  const run = task ? runForTask(runs, task, task.agent_name) : null;
  const handoff = task ? handoffForTask(handoffs, task, task.agent_name) : null;
  return {
    actorKind: overrides.actorKind || "agent",
    role: overrides.role || (task && `${task.role || "AGENT"} · ATTEMPT ${task.attempt || 1}`) || "AGENT",
    name: overrides.name || (task && task.agent_name) || "NOT_OBSERVED",
    status: overrides.status || (run && run.status) || "NOT_OBSERVED",
    tone: overrides.tone || ((run && run.status === "TRUSTED_COMPLETE") ? "pass" : "replan"),
    dependsOn: task && task.depends_on,
    inputRefs: (task && task.input_refs) || (handoff && handoff.input_refs),
    inputDigest: task && task.input_digest || run && run.input_digest,
    digest: task && (task.binding_digest || task.digest) || handoff && handoff.digest,
    outputDigest: task && task.output_digest || run && run.output_digest,
    model: run && run.model_version,
    modelProvider: run && run.model_provider,
    providerRequestId: run && run.provider_request_id,
    modelAuthority: run && run.model_claim_boundary,
    tool: run && run.tool_versions,
    skill: run && run.skill_versions,
    trace: run && run.trace_id,
    candidateOnly: task && task.candidate_only,
    writes: run ? run.target_writes ?? 0 : task && task.target_writes,
    evidenceMode: run && run.evidence_class,
    taskSummary: overrides.taskSummary,
    contextSummary: overrides.contextSummary,
    outputSummary: overrides.outputSummary,
    handoffTo: overrides.handoffTo || (handoff && handoff.to_agent),
    reviewDecision: overrides.reviewDecision,
    effectSummary: overrides.effectSummary,
  };
}

function nativeLifecycleState(observedCount, totalCount, sameRun = true) {
  if (!sameRun || totalCount <= 0 || observedCount <= 0) return "waiting";
  return observedCount >= totalCount ? "observed" : "partial";
}

function nativeLifecycleStep(index, title, detail, state) {
  const stateKey = {
    observed: "lifecycle.state.observed",
    partial: "lifecycle.state.partial",
    waiting: "lifecycle.state.waiting",
  }[state] || "lifecycle.state.waiting";
  return `<article class="native-lifecycle-step ${escapeHtml(state)}">
    <span>${String(index).padStart(2, "0")}</span>
    <strong>${escapeHtml(title)}</strong>
    <small>${escapeHtml(detail)}</small>
    <b>${escapeHtml(t(stateKey))}</b>
  </article>`;
}

function hideGoldenNativeLifecycle() {
  const panel = byId("active-native-lifecycle");
  if (panel) panel.hidden = true;
}

function renderGoldenNativeLifecycle(competition, collaboration, tasks, runs, handoffs) {
  const panel = byId("active-native-lifecycle");
  if (!panel) return;

  const plan = collaboration.orchestration_plan || {};
  const nativeTasks = tasks.filter((task) => task.role === "DOMAIN_WORKER" || task.role === "REVIEWER");
  const nativeTaskIds = new Set(nativeTasks.map((task) => task.id));
  const nativeRuns = runs.filter((run) => nativeTaskIds.has(run.task_id));
  const nativeHandoffs = handoffs.filter((handoff) => nativeTaskIds.has(handoff.task_id));
  const totalTasks = nativeTasks.length;
  const sameRun = Boolean(competition.run_id && plan.run_id === competition.run_id);
  const revisions = Array.isArray(plan.plan_revisions) ? plan.plan_revisions.length : 0;
  const actionCount = Number(competition.agentteams_action_count) || 0;
  const terminalStatus = String(competition.project_terminal_state || "NOT_OBSERVED");
  const projectObserved = sameRun && plan.status === "COMPLETED" && totalTasks > 0;

  // A projected binding_digest is created only after the server has verified
  // delegate -> ACK -> subprocess -> submit -> check -> accept for that task.
  // Keep the browser on that compact server fact instead of inventing a second
  // client-side task authority or reconstructing unprojected action digests.
  const completedBindingCount = nativeTasks.filter((task) => {
    const run = nativeRuns.find((item) => item.task_id === task.id);
    const handoff = nativeHandoffs.find((item) => item.task_id === task.id);
    return Boolean(
      task.binding_digest
      && task.input_digest
      && task.output_digest
      && run
      && run.output_digest === task.output_digest
      && handoff
      && handoff.payload
      && handoff.payload.output_digest === task.output_digest,
    );
  }).length;
  const contextBindingCount = nativeTasks.filter((task) => task.input_digest).length;
  const resultHandoffCount = nativeTasks.filter((task) => {
    const handoff = nativeHandoffs.find((item) => item.task_id === task.id);
    return Boolean(handoff && handoff.payload && handoff.payload.output_digest === task.output_digest);
  }).length;
  const bindingState = nativeLifecycleState(completedBindingCount, totalTasks, sameRun);
  const handoffState = nativeLifecycleState(
    Math.min(contextBindingCount, resultHandoffCount),
    totalTasks,
    sameRun,
  );
  const terminalObserved = sameRun && terminalStatus.toLowerCase() === "completed" && actionCount > 0;

  const stages = [
    {
      title: t("lifecycle.step.project"),
      detail: t("lifecycle.step.project.detail", { revisions, tasks: totalTasks }),
      state: projectObserved ? "observed" : "waiting",
    },
    {
      title: t("lifecycle.step.delegate"),
      detail: t("lifecycle.step.delegate.detail", { count: completedBindingCount, total: totalTasks }),
      state: bindingState,
    },
    {
      title: t("lifecycle.step.ack"),
      detail: t("lifecycle.step.ack.detail", { count: completedBindingCount, total: totalTasks }),
      state: bindingState,
    },
    {
      title: t("lifecycle.step.handoff"),
      detail: t("lifecycle.step.handoff.detail", {
        context: contextBindingCount,
        result: resultHandoffCount,
        total: totalTasks,
      }),
      state: handoffState,
    },
    {
      title: t("lifecycle.step.submitCheck"),
      detail: t("lifecycle.step.submitCheck.detail", { count: completedBindingCount, total: totalTasks }),
      state: bindingState,
    },
    {
      title: t("lifecycle.step.received"),
      detail: t("lifecycle.step.received.detail", { count: completedBindingCount, total: totalTasks }),
      state: bindingState,
    },
    {
      title: t("lifecycle.step.terminal"),
      detail: t("lifecycle.step.terminal.detail", { status: displayToken(terminalStatus), actions: actionCount }),
      state: terminalObserved ? "observed" : "waiting",
    },
  ];

  text("active-native-lifecycle-run", t("lifecycle.run", {
    runId: competition.run_id || "NOT_OBSERVED",
    actions: actionCount,
  }));
  exactAuditTitle("active-native-lifecycle-run");
  text("active-native-lifecycle-boundary", t("lifecycle.boundary"));
  byId("active-native-lifecycle-rail").innerHTML = stages
    .map((stage, index) => nativeLifecycleStep(index + 1, stage.title, stage.detail, stage.state))
    .join("");
  panel.hidden = false;
}

const AGENT_WORK_PROFILES = Object.freeze([
  {
    actor: "change-coordinator",
    role: "COORDINATOR",
    taskKey: "agentWork.task.coordinator",
    effectKey: "agentWork.effect.coordinator",
  },
  {
    actor: "product-steward",
    domain: "product",
    role: "PRODUCT",
    taskKey: "agentWork.task.product",
    effectKey: "agentWork.effect.product",
  },
  {
    actor: "legal-steward",
    domain: "legal",
    role: "LEGAL",
    taskKey: "agentWork.task.legal",
    effectKey: "agentWork.effect.legal",
  },
  {
    actor: "finance-steward",
    domain: "finance",
    role: "FINANCE",
    taskKey: "agentWork.task.finance",
    effectKey: "agentWork.effect.finance",
  },
  {
    actor: "gtm-steward",
    domain: "gtm",
    role: "GTM",
    taskKey: "agentWork.task.gtm",
    effectKey: "agentWork.effect.gtm",
  },
  {
    actor: "independent-reviewer",
    domain: "review",
    role: "REVIEWER",
    taskKey: "agentWork.task.reviewer",
    effectKey: "agentWork.effect.reviewer",
  },
]);

const AGENT_WORK_ACTIVITY_PLANES = new Set([
  "AGENTTEAMS",
  "REVIEWER",
  "TOOL",
  "SKILL",
  "ADVISORY",
  "HANDOFF",
]);

function hideAgentWorkObservability() {
  const panel = byId("agent-work-observability");
  if (!panel) return;
  panel.hidden = true;
  panel.removeAttribute("data-run-id");
  byId("agent-work-facts").innerHTML = "";
  byId("agent-work-list").innerHTML = "";
}

function agentWorkActivityRows(rows, runId, actor) {
  return rows.filter((row) => {
    if (!sameRunActivityRow(row, runId) || !AGENT_WORK_ACTIVITY_PLANES.has(row.plane)) return false;
    const observedActor = String(row.actor_or_domain || "");
    return observedActor === actor || observedActor.startsWith(`${actor}→`);
  });
}

function agentWorkTasks(tasks, actor) {
  return tasks.filter((task) => task && task.agent_name === actor);
}

function agentWorkTaskRuns(runs, actorTasks, actor) {
  const taskIds = new Set(actorTasks.map((task) => task.id));
  return runs.filter((run) => taskIds.has(run.task_id) || (!run.task_id && run.agent_name === actor));
}

function agentWorkTaskReceipts(actorTasks, actorRuns) {
  if (!actorTasks.length) return `<p>${localizedFactHtml("NOT_OBSERVED")}</p>`;
  return `<ul class="agent-work-task-receipts">${actorTasks.map((task) => {
    const run = actorRuns.find((item) => item.task_id === task.id);
    const copy = task.role
      ? t("agentWork.taskReceipt", {
        task: task.id,
        attempt: task.attempt || 1,
        status: displayToken(run && run.status || "NOT_OBSERVED"),
      })
      : t("agentWork.managerReceipt", {
        task: task.id,
        status: displayToken(run && run.status || "NOT_OBSERVED"),
      });
    const exactReview = task.role === "REVIEWER" && run && run.role === "REVIEWER"
      && run.agent_name === task.agent_name && run.attempt === task.attempt
      && isSha256Digest(task.input_digest) && run.input_digest === task.input_digest
      && isSha256Digest(task.output_digest) && run.output_digest === task.output_digest;
    const reviewFacts = exactReview ? [
      run.independent_process === true ? t("agentWork.reviewProcess") : null,
      typeof run.model_advisory_accepted === "boolean"
        ? t(`agentWork.modelAdvice.${run.model_advisory_accepted}`) : null,
    ].filter(Boolean) : [];
    const decisionText = (decision) => {
      if (!decision || !["PASS", "REPLAN"].includes(decision.verdict)
        || !Array.isArray(decision.missing_domains)
        || !decision.missing_domains.every((domain) => ["product", "legal", "finance", "gtm"].includes(domain))) {
        return t("agentWork.review.unknown");
      }
      const domains = decision.missing_domains.length
        ? [...new Set(decision.missing_domains)].map((domain) => t(`domain.${domain}`)).join(", ")
        : t("agentWork.review.none");
      return `${displayToken(decision.verdict)} · ${t("agentWork.review.missing", { domains })}`;
    };
    const reviewDetail = exactReview
      ? `<div class="agent-work-review-process">${escapeHtml(reviewFacts.join(" · "))}
        <dl class="agent-work-review-decisions">
          <div><dt>${escapeHtml(t("agentWork.review.model"))}</dt><dd>${escapeHtml(decisionText(run.model_advisory))}</dd></div>
          <div><dt>${escapeHtml(t("agentWork.review.deterministic"))}</dt><dd>${escapeHtml(decisionText(run.deterministic_review))}</dd></div>
        </dl><small>${escapeHtml(t("agentWork.review.authority"))} ${escapeHtml(t("agentWork.reviewProcessBoundary"))}</small></div>`
      : "";
    return `<li title="${escapeHtml(task.input_digest || run && run.input_digest || "")}">${escapeHtml(copy)}${reviewDetail}</li>`;
  }).join("")}</ul>`;
}

function agentWorkCapabilityEntries(rows) {
  const entries = [];
  rows.forEach((row) => {
    const raw = String(row.tool_or_skill || "").trim();
    if (!raw) return;
    raw.split(/\s*,\s*/).filter(Boolean).forEach((value) => {
      if (String(value).toLowerCase().startsWith("tool:none")) return;
      const entry = {
        plane: row.plane,
        action: row.action,
        sourceRef: row.source_ref,
        value,
      };
      if (!entries.some((existing) => (
        existing.plane === entry.plane
          && existing.action === entry.action
          && existing.value === entry.value
      ))) {
        entries.push(entry);
      }
    });
  });
  return entries;
}

function isObservedLoadedSkill(entry) {
  const value = String(entry && entry.value || "").toLowerCase();
  return entry && entry.plane === "SKILL" && value.startsWith("skill-package:");
}

function agentWorkCapabilityPresentation(entry) {
  const plane = String(entry && entry.plane || "").toUpperCase();
  const action = String(entry && entry.action || "").toUpperCase();
  const value = String(entry && entry.value || "");
  const normalized = value.toLowerCase();
  if (plane === "TOOL" && action === "RECOVER_FINANCE_DEPENDENCY") {
    return {
      kind: "business-recovery-tool",
      label: t("agentWork.capability.businessRecoveryTool"),
      detail: t("agentWork.capability.businessRecoveryTool.detail"),
    };
  }
  if (plane === "TOOL" && action === "READ_DEPENDENCY_EVIDENCE") {
    return {
      kind: "post-formation-audit-tool",
      label: t("agentWork.capability.postFormationAuditTool"),
      detail: t("agentWork.capability.postFormationAuditTool.detail"),
    };
  }
  if (plane === "SKILL" && normalized.startsWith("skill-package:")) {
    return {
      kind: "loaded-skill-package",
      label: t("agentWork.capability.loadedSkill"),
      detail: "",
    };
  }
  if (plane === "ADVISORY" && normalized.startsWith("skill:")) {
    return {
      kind: "advisory-skill-ref",
      label: t("agentWork.capability.advisorySkillRef"),
      detail: "",
    };
  }
  if (plane === "AGENTTEAMS" && normalized.includes("controlled_local_real_http")) {
    return {
      kind: "business-recovery-binding",
      label: t("agentWork.capability.businessRecoveryBinding"),
      detail: "",
    };
  }
  if (plane === "AGENTTEAMS") {
    return {
      kind: "agentteams-runtime",
      label: t("agentWork.capability.runtime"),
      detail: "",
    };
  }
  return {
    kind: plane.toLowerCase() || "runtime-capability",
    label: displayToken(plane || "NOT_OBSERVED"),
    detail: "",
  };
}

function agentWorkCapabilities(rows) {
  const entries = agentWorkCapabilityEntries(rows);
  const observedSkill = entries.some(isObservedLoadedSkill);
  const observed = entries.length
    ? entries.map((entry) => {
      const presentation = agentWorkCapabilityPresentation(entry);
      return `<li data-capability-kind="${escapeHtml(presentation.kind)}">
        <b>${escapeHtml(presentation.label)}</b>
        <span>${escapeHtml(entry.value)}</span>
        ${presentation.detail ? `<small>${escapeHtml(presentation.detail)}</small>` : ""}
      </li>`;
    }).join("")
    : `<li class="unobserved"><span>${escapeHtml(t("agentWork.noCapability"))}</span></li>`;
  const skillBoundary = observedSkill
    ? ""
    : `<li class="unobserved skill-boundary"><span>${escapeHtml(t("agentWork.noLoadedSkill"))}</span></li>`;
  return `<ul class="agent-work-capabilities">${observed}${skillBoundary}</ul>`;
}

function contextExclusionSummary(projection) {
  const counts = projection && projection.excluded_reason_counts;
  if (!counts || typeof counts !== "object" || Array.isArray(counts)) return "";
  const labels = {
    OTHER_AUTHORITY_DOMAIN: "otherDomain",
    MINIMAL_DISCLOSURE_DERIVATION_ONLY: "derivedOnly",
    FORBIDDEN_OUTPUT_FIELD: "forbidden",
    SENSITIVITY_CEILING_EXCEEDED: "sensitivity",
    NOT_REQUIRED_FOR_TASK: "notRequired",
    OTHER: "other",
  };
  const entries = Object.entries(counts);
  if (!entries.length || entries.some(([reason, count]) => (
    !Object.hasOwn(labels, reason) || !Number.isSafeInteger(count) || count <= 0
  )) || entries.reduce((total, [, count]) => total + count, 0) !== projection.excluded_count) return "";
  return t("dataJourney.context.reasons", {
    reasons: entries.map(([reason, count]) => t(`dataJourney.context.reason.${labels[reason]}`, { count })).join(" · "),
  });
}

function agentWorkContext(lineage, profile) {
  const actors = lineage && lineage.context && Array.isArray(lineage.context.actors)
    ? lineage.context.actors
    : [];
  const projection = actors.find((actor) => actor.actor_id === profile.actor);
  if (!projection) return `<p>${escapeHtml(t("agentWork.context.control"))}</p>`;
  const slotIds = Array.isArray(projection.included_slot_ids) ? projection.included_slot_ids : [];
  const slots = slotIds.length
    ? slotIds.map(dataJourneySlotLabel).join(" · ")
    : t("dataJourney.context.noSlots");
  const reasons = contextExclusionSummary(projection);
  return `<p title="${escapeHtml(projection.projection_digest || "")}">${escapeHtml(t("agentWork.context", {
    included: projection.included_count,
    excluded: projection.excluded_count,
    slots,
  }))}</p>${reasons ? `<p class="agent-work-context-reasons">${escapeHtml(reasons)}</p>` : ""}`;
}

function agentWorkNativeHandoffs(handoffs, actor) {
  return handoffs.filter((handoff) => handoff && handoff.from_agent === actor);
}

function agentWorkObservedHandoffs(nativeHandoffs, rows) {
  const items = [];
  nativeHandoffs.forEach((handoff) => {
    const payload = handoff.payload || {};
    items.push({
      kind: payload.kind || "CANDIDATE",
      target: handoff.to_agent || "NOT_OBSERVED",
      writes: payload.target_writes ?? 0,
      digest: payload.output_digest || handoff.digest,
    });
  });
  rows.filter((row) => row.plane === "HANDOFF").forEach((row) => {
    const actorParts = String(row.actor_or_domain || "").split("→");
    items.push({
      kind: row.action || "CANDIDATE",
      target: actorParts[1] || "NOT_OBSERVED",
      writes: row.target_writes ?? 0,
      digest: row.receipt_digest,
    });
  });
  return items.filter((item, index) => items.findIndex((candidate) => (
    candidate.kind === item.kind
      && candidate.target === item.target
      && candidate.digest === item.digest
  )) === index);
}

function agentWorkCandidateView(state) {
  const view = state && state.agent_candidate_outputs;
  const runId = currentRunId(state || {});
  if (!(
    view
      && view.status === "PASS"
      && view.run_id === runId
      && view.candidate_only === true
      && view.target_writes === 0
      && Array.isArray(view.outputs)
  )) return null;
  return view;
}

function agentWorkCandidateValue(value) {
  if (value && typeof value === "object") return JSON.stringify(value);
  return dataJourneyObserved(value);
}

function agentWorkCandidateAttempt(output) {
  const decision = output && output.decision;
  const status = decision && decision.verdict || output.status || "NOT_OBSERVED";
  const missingFields = Array.isArray(output.missing_fields) ? output.missing_fields : [];
  const reasonCodes = decision && Array.isArray(decision.reason_codes)
    ? decision.reason_codes
    : Array.isArray(output.reason_codes) ? output.reason_codes : [];
  const missingDomains = decision && Array.isArray(decision.missing_domains)
    ? decision.missing_domains
    : [];
  const candidates = Array.isArray(output.candidate_outputs) ? output.candidate_outputs : [];
  const candidateRows = candidates.map((candidate) => {
    const isCapabilityRequirement = String(candidate.semantic_kind || "").toUpperCase() === "SKILL";
    return `<li data-candidate-predicate="${escapeHtml(candidate.predicate || "NOT_OBSERVED")}" data-candidate-source-ref="${escapeHtml(candidate.source_ref || "NOT_OBSERVED")}" title="${escapeHtml(candidate.candidate_digest || "")}">
      <div><span>${escapeHtml(dataJourneySlotLabel(candidate.predicate))}</span><em>${escapeHtml(dataJourneySensitivityLabel(candidate.sensitivity))}</em></div>
      <strong>${escapeHtml(agentWorkCandidateValue(candidate.value))}</strong>
      <small>${escapeHtml(t("agentWork.candidateProvenance", {
        source: candidate.source_ref || "NOT_OBSERVED",
        version: candidate.version || "NOT_OBSERVED",
      }))}</small>
      ${candidate.transformation_ref ? `<small class="candidate-transformation">${escapeHtml(t("agentWork.candidateTransformation", { transformation: candidate.transformation_ref }))}</small>` : ""}
      ${isCapabilityRequirement ? `<small class="capability-requirement-ref">${escapeHtml(t("dataJourney.source.capabilityRequirement"))}</small>` : ""}
    </li>`;
  }).join("");
  const reviewBlock = decision ? `<div class="agent-work-review-decision" data-review-verdict="${escapeHtml(decision.verdict || "NOT_OBSERVED")}">
    <strong>${escapeHtml(t("agentWork.reviewVerdict", { verdict: displayToken(decision.verdict || "NOT_OBSERVED") }))}</strong>
    ${missingDomains.length ? `<span>${escapeHtml(t("agentWork.reviewMissing", { domains: missingDomains.map(displayToken).join(" · ") }))}</span>` : ""}
  </div>` : "";
  return `<article class="agent-work-candidate-attempt" data-task-id="${escapeHtml(output.task_id || "NOT_OBSERVED")}" data-attempt="${escapeHtml(output.attempt || "NOT_OBSERVED")}" data-status="${escapeHtml(status)}" title="${escapeHtml(output.output_digest || "")}">
    <header>
      <strong>${escapeHtml(t("agentWork.candidateAttempt", { attempt: output.attempt || "?", status: displayToken(status) }))}</strong>
      <small>${escapeHtml(t("agentWork.executorRef", { executor: output.executor_ref || output.actor_id || "NOT_OBSERVED" }))}</small>
    </header>
    ${reviewBlock}
    ${candidateRows ? `<ul class="agent-work-candidate-values">${candidateRows}</ul>` : ""}
    ${missingFields.length ? `<p class="agent-work-candidate-missing">${escapeHtml(t("agentWork.candidateMissing", { fields: missingFields.map(dataJourneySlotLabel).join(" · ") }))}</p>` : ""}
    ${reasonCodes.length ? `<p class="agent-work-candidate-reasons">${escapeHtml(t("agentWork.candidateReasons", { reasons: reasonCodes.map(displayToken).join(" · ") }))}</p>` : ""}
  </article>`;
}

function agentWorkOutput(state, profile) {
  if (profile.actor === "change-coordinator") {
    return `<div class="agent-work-candidate-summary" data-candidate-view-status="CONTROL_ONLY"><p>${escapeHtml(t("agentWork.output.coordinator"))}</p><small>${escapeHtml(t("agentWork.candidateBoundary"))}</small></div>`;
  }
  const view = agentWorkCandidateView(state);
  if (!view) return `<p>${escapeHtml(t("agentWork.candidateUnavailable"))}</p>`;
  const outputs = view.outputs
    .filter((output) => output && output.actor_id === profile.actor)
    .sort((left, right) => Number(left.attempt) - Number(right.attempt));
  if (!outputs.length) return `<p>${escapeHtml(t("agentWork.candidateUnavailable"))}</p>`;
  return `<div class="agent-work-candidate-summary" data-candidate-view-status="PASS">
    <header><strong>${escapeHtml(t("agentWork.candidateVerified"))}</strong><small>${escapeHtml(t("agentWork.candidateBoundary"))}</small></header>
    <div>${outputs.map(agentWorkCandidateAttempt).join("")}</div>
  </div>`;
}

function agentWorkHandoffList(observedHandoffs) {
  if (!observedHandoffs.length) return `<p>${localizedFactHtml("NOT_OBSERVED")}</p>`;
  return `<ul class="agent-work-handoffs">${observedHandoffs.map((handoff) => `<li title="${escapeHtml(handoff.digest || "")}">${escapeHtml(t("agentWork.handoffReceipt", {
    kind: displayToken(handoff.kind),
    target: displayToken(handoff.target),
    writes: handoff.writes,
  }))}</li>`).join("")}</ul>`;
}

function agentWorkRelatedObjects(state, lineage, profile) {
  const sourceValues = lineage && lineage.source && Array.isArray(lineage.source.values)
    ? lineage.source.values
    : [];
  const objects = sourceValues
    .filter((value) => profile.domain && value.domain_id === profile.domain && value.slot_id !== "public_message")
    .map((value) => value.object_ref)
    .filter(Boolean);
  Object.keys(state.changes || {}).forEach((kind) => {
    const receipt = receiptForChange(state.changes && state.changes[kind]);
    const applied = receipt && Array.isArray(receipt.applied_claims) ? receipt.applied_claims : [];
    applied.forEach((claim) => {
      if (profile.domain && String(claim.object_id || "").includes(`:${profile.domain}.`)) objects.push(claim.object_id);
    });
  });
  if (["change-coordinator", "gtm-steward", "independent-reviewer"].includes(profile.actor) && state.quote && state.quote.id) {
    objects.push(`${state.quote.id}@${state.quote.version}`);
  }
  const unique = [...new Set(objects)];
  const exact = unique.length ? unique.join(" · ") : displayToken("NOT_OBSERVED");
  return `<p>${escapeHtml(t(profile.effectKey))}</p><small title="${escapeHtml(unique.join(" · "))}">${escapeHtml(t("agentWork.relatedObjects", { objects: exact }))}</small>`;
}

function agentWorkTimeline(rows) {
  if (!rows.length) return `<p>${localizedFactHtml("NOT_OBSERVED")}</p>`;
  return `<ol class="agent-work-timeline">${[...rows].sort((left, right) => Number(left.sequence) - Number(right.sequence)).map((row) => {
    const capabilityEntry = agentWorkCapabilityEntries([row])[0];
    const capabilityPresentation = capabilityEntry
      ? agentWorkCapabilityPresentation(capabilityEntry)
      : null;
    const capability = capabilityEntry
      ? `<span data-capability-kind="${escapeHtml(capabilityPresentation.kind)}">${escapeHtml(capabilityPresentation.label)} · ${escapeHtml(capabilityEntry.value)}</span>`
      : "";
    const receipt = row.receipt_digest
      ? `<code title="${escapeHtml(row.receipt_digest)}">${escapeHtml(t("agentWork.timelineEvidence", { receipt: shortDigest(row.receipt_digest, 24) }))}</code>`
      : "";
    return `<li data-plane="${escapeHtml(row.plane)}">
      <div><strong>${escapeHtml(t("agentWork.timelineReceipt", {
        sequence: row.sequence,
        plane: displayToken(row.plane),
        status: displayToken(row.status),
      }))}</strong>${receipt}</div>
      <p>${localizedFactHtml(row.action || "NOT_OBSERVED")}${capability}</p>
    </li>`;
  }).join("")}</ol>`;
}

function agentWorkCard(state, lineage, profile, tasks, runs, handoffs, rows) {
  const actorTasks = agentWorkTasks(tasks, profile.actor);
  const actorRuns = agentWorkTaskRuns(runs, actorTasks, profile.actor);
  const nativeHandoffs = agentWorkNativeHandoffs(handoffs, profile.actor);
  const observedHandoffs = agentWorkObservedHandoffs(nativeHandoffs, rows);
  const attemptSummary = actorTasks.length
    ? actorTasks.map((task) => {
      const run = actorRuns.find((item) => item.task_id === task.id);
      const attempt = task.role ? `A${task.attempt || 1}` : "AT";
      return `${attempt} ${displayToken(run && run.status || "NOT_OBSERVED")}`;
    }).join(" → ")
    : displayToken("NOT_OBSERVED");
  return `<details class="agent-work-card" data-agent-actor="${escapeHtml(profile.actor)}">
    <summary>
      <span>${escapeHtml(displayToken(profile.role))}</span>
      <strong>${escapeHtml(displayToken(profile.actor))}</strong>
      <small>${escapeHtml(attemptSummary)}</small>
      <em>${escapeHtml(t("agentWork.card.activities", { count: rows.length }))}</em>
      <b aria-hidden="true">+</b>
    </summary>
    <div class="agent-work-card-body">
      <section class="agent-work-field"><h4>${escapeHtml(t("agentWork.field.task"))}</h4><p>${escapeHtml(t(profile.taskKey))}</p>${agentWorkTaskReceipts(actorTasks, actorRuns)}</section>
      <section class="agent-work-field"><h4>${escapeHtml(t("agentWork.field.context"))}</h4>${agentWorkContext(lineage, profile)}</section>
      <section class="agent-work-field"><h4>${escapeHtml(t("agentWork.field.capability"))}</h4>${agentWorkCapabilities(rows)}</section>
      <section class="agent-work-field agent-work-output-field"><h4>${escapeHtml(t("agentWork.field.output"))}</h4>${agentWorkOutput(state, profile)}</section>
      <section class="agent-work-field"><h4>${escapeHtml(t("agentWork.field.handoff"))}</h4>${agentWorkHandoffList(observedHandoffs)}</section>
      <section class="agent-work-field"><h4>${escapeHtml(t("agentWork.field.effect"))}</h4>${agentWorkRelatedObjects(state, lineage, profile)}</section>
      <section class="agent-work-field agent-work-timeline-field"><h4>${escapeHtml(t("agentWork.field.timeline"))}</h4>${agentWorkTimeline(rows)}</section>
      <p class="agent-work-card-boundary">${escapeHtml(t("agentWork.candidateOnly"))}</p>
    </div>
  </details>`;
}

function renderAgentWorkObservability(state, competition, collaboration, tasks, runs, handoffs) {
  const panel = byId("agent-work-observability");
  if (!panel) return;
  const runId = competition && competition.run_id;
  const allRows = state.execution_activity && Array.isArray(state.execution_activity.rows)
    ? state.execution_activity.rows
    : [];
  const actors = AGENT_WORK_PROFILES.map((profile) => ({
    profile,
    tasks: agentWorkTasks(tasks, profile.actor),
    rows: agentWorkActivityRows(allRows, runId, profile.actor),
  })).filter((entry) => entry.tasks.length || entry.rows.length);
  if (!runId || !actors.length) {
    hideAgentWorkObservability();
    return;
  }
  const nativeTasks = tasks.filter((task) => task.role === "DOMAIN_WORKER" || task.role === "REVIEWER");
  const activityCount = actors.reduce((total, actor) => total + actor.rows.length, 0);
  const targetWrites = actors.reduce((total, actor) => total + actor.rows.reduce((subtotal, row) => (
    subtotal + (Number.isFinite(Number(row.target_writes)) ? Number(row.target_writes) : 0)
  ), 0), 0);
  panel.dataset.runId = runId;
  text("agent-work-summary", t("agentWork.summary", {
    actors: actors.length,
    tasks: nativeTasks.length,
    activities: activityCount,
  }));
  exactAuditTitle("agent-work-summary", runId);
  byId("agent-work-facts").innerHTML = [
    ["agentWork.fact.agents", t("agentWork.fact.value", { count: actors.length })],
    ["agentWork.fact.tasks", t("agentWork.fact.value", { count: nativeTasks.length })],
    ["agentWork.fact.writes", targetWrites === 0 ? t("agentWork.fact.zeroWrites") : targetWrites],
  ].map(([labelKey, value]) => `<article><span>${escapeHtml(t(labelKey))}</span><strong>${escapeHtml(value)}</strong></article>`).join("");
  byId("agent-work-list").innerHTML = actors.map(({ profile, rows }) => agentWorkCard(
    state,
    state.enterprise_data_lineage || {},
    profile,
    tasks,
    runs,
    handoffs,
    rows,
  )).join("");
  panel.hidden = false;
}

function renderGoldenCollaboration(state, competition) {
  const collaboration = competition.agent_collaboration || {};
  const plan = collaboration.orchestration_plan || {};
  const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
  const runs = Array.isArray(collaboration.agent_runs) ? collaboration.agent_runs : [];
  const handoffs = Array.isArray(collaboration.handoffs) ? collaboration.handoffs : [];
  const reviewer = collaboration.reviewer || {};
  const reviewerProvider = reviewer.model_provider || competition.model_provider || "NOT_OBSERVED";
  const reviewerModel = reviewer.model_version || "NOT_OBSERVED";
  const reviewerSuggestion = modelSuggestion(
    reviewerProvider,
    reviewer.model_evidence_class || reviewer.provider_evidence_class || competition.provider_evidence_class,
  );
  const tool = collaboration.tool || {};
  const skill = collaboration.skill || {};
  const activityRows = Array.isArray(state.execution_activity && state.execution_activity.rows)
    ? state.execution_activity.rows
    : [];
  const managerTask = tasks.find((task) => task.role === "MANAGER")
    || tasks.find((task) => task.agent_name === "change-coordinator");
  const managerRun = managerTask ? runForTask(runs, managerTask, managerTask.agent_name) : null;
  const domainAttemptOne = tasks.filter((task) => task.role === "DOMAIN_WORKER" && task.attempt === 1);
  const financeAttemptTwo = tasks.find((task) => task.role === "DOMAIN_WORKER" && task.authority_domain === "finance" && task.attempt === 2);
  const gtmTask = tasks.find((task) => task.role === "DOMAIN_WORKER" && task.authority_domain === "gtm" && task.attempt === 1);
  const reviewerAttemptOne = tasks.find((task) => task.role === "REVIEWER" && task.attempt === 1);
  const reviewerAttemptTwo = tasks.find((task) => task.role === "REVIEWER" && task.attempt === 2);
  const toolActivity = exactFinanceToolInvocation(activityRows, competition.run_id, tool, financeAttemptTwo);
  const skillActivity = exactGtmSkillInvocation(activityRows, competition.run_id, skill, gtmTask);
  const approval = activeApproval(state);
  const preview = activePreview(state);
  const latestOutcome = state.latest_outcome || null;
  const quote = state.quote || {};
  const approvalDigest = approval && (approval.approval_digest || approval.artifact_digest || approval.approval && approval.approval.digest);
  const approvalOwner = approvalActor(approval) || state.actions && state.actions.owner_id;
  const skillQualificationReady = skill.evaluation_partition_count === 8
    && !Object.hasOwn(skill, "evaluation_case_count")
    && !Object.hasOwn(skill, "qualification_suite_revision") && !Object.hasOwn(skill, "qualification_suite_digest")
    || skill.evaluation_partition_count === 8 && skill.evaluation_case_count === 9
    && skill.qualification_suite_revision === "orgrebase.quote-skill-qualification.v2"
    && skill.qualification_suite_digest === "sha256:7b272a0f3da13f2c8d7db1bd87afd797cbca28e4e3e5c569120a8e7f54e19ea7";
  const skillReleaseReady = skill.authorization_mode === "RELEASE"
    && skill.release_state === "CANARY"
    && skillQualificationReady
    && Boolean(skill.release_receipt_digest);
  const oacLineage = competition.oac_agentteams_lineage || {};
  const oacExecutionBound = oacLineage.status === "OAC_BOUND_EXECUTION_PLAN_REALIZED"
    && oacLineage.topology_match === true
    && Array.isArray(oacLineage.planned_domain_ids)
    && oacLineage.planned_domain_ids.length > 0;
  const oacFormationNode = oacExecutionBound ? {
    actorKind: "oac-compiler",
    role: t("topology.role.oacFormation"),
    name: t("topology.name.oacFormation"),
    status: t("topology.status.oacFormation", {
      domains: oacLineage.planned_domain_ids.length,
    }),
    tone: "control",
    dependsOn: [],
    inputRefs: [
      oacLineage.organization_snapshot_digest,
      oacLineage.organizational_demand_digest,
    ].filter(Boolean),
    inputDigest: oacLineage.task_formation_decision_receipt_digest,
    digest: oacLineage.task_agent_context_envelope_digest,
    outputDigest: oacLineage.agentteams_execution_plan_digest,
    model: t("topology.detail.oacCompiler"),
    tool: null,
    skill: null,
    trace: competition.summary_digest,
    candidateOnly: true,
    writes: 0,
    evidenceMode: "OAC_BOUND_CURRENT_RUN",
    taskSummary: t("topology.summary.oac.task"),
    contextSummary: t("topology.summary.oac.context"),
    outputSummary: t("topology.summary.oac.output"),
    handoffTo: t("topology.name.managerRole"),
    effectSummary: t("topology.summary.oac.effect"),
  } : null;

  const managerNode = {
    actorKind: "control",
    role: t("topology.role.manager"),
    name: t("topology.name.managerRole"),
    status: t("topology.status.managerRole"),
    tone: "control",
    dependsOn: oacFormationNode ? [oacLineage.agentteams_execution_plan_digest] : [],
    inputRefs: managerTask && managerTask.input_refs,
    inputDigest: managerRun && managerRun.input_digest,
    digest: plan.digest,
    outputDigest: managerRun && managerRun.output_digest,
    model: managerRun && managerRun.model_version || "NONE_DETERMINISTIC_MANAGER",
    tool: null,
    skill: null,
    trace: managerRun && managerRun.trace_id,
    candidateOnly: true,
    writes: 0,
    evidenceMode: t("topology.detail.managerBoundary"),
    taskSummary: t("topology.summary.manager.task"),
    contextSummary: t("topology.summary.manager.context"),
    outputSummary: t("topology.summary.manager.output"),
    handoffTo: t("topology.summary.manager.handoff"),
    effectSummary: t("topology.summary.manager.effect"),
  };
  const workerNodes = domainAttemptOne.map((task) => {
    const domain = displayToken(String(task.authority_domain || "domain").toUpperCase());
    const inputCount = Array.isArray(task.input_refs) ? task.input_refs.filter(Boolean).length : 0;
    return goldenTaskNode(task, runs, handoffs, {
      role: t("topology.role.workerA1", { domain }),
      status: task.authority_domain === "finance"
        ? t("topology.status.atReceivedAbstain")
        : t("topology.status.atReceived"),
      tone: task.authority_domain === "finance" ? "replan" : "pass",
      taskSummary: t("topology.summary.domain.task", { domain }),
      contextSummary: t("topology.summary.domain.context", { domain, count: inputCount }),
      outputSummary: t(task.authority_domain === "finance"
        ? "topology.summary.domain.abstain"
        : "topology.summary.domain.output", { domain }),
      effectSummary: t("topology.summary.domain.effect"),
    });
  });
  const reviewerOneNode = goldenTaskNode(reviewerAttemptOne, runs, handoffs, {
    role: t("topology.role.reviewerA1"),
    status: t("topology.status.atReceivedDecision", {
      decision: displayToken(reviewer.attempt_1 && reviewer.attempt_1.verdict || "REPLAN"),
    }),
    tone: "replan",
    taskSummary: t("topology.summary.review1.task"),
    contextSummary: t("topology.summary.review1.context"),
    outputSummary: t("topology.summary.review1.output"),
    reviewDecision: t("topology.summary.review1.decision"),
    effectSummary: t("topology.summary.review1.effect"),
  });
  const financeTwoNode = goldenTaskNode(financeAttemptTwo, runs, handoffs, {
    role: t("topology.role.workerA2"),
    name: toolActivity && toolActivity.actor_or_domain || financeAttemptTwo && financeAttemptTwo.agent_name,
    status: toolActivity ? t("topology.status.toolRecovered") : "NOT_OBSERVED",
    tone: toolActivity ? "pass" : "not-run",
    taskSummary: t("topology.summary.finance2.task"),
    contextSummary: t("topology.summary.finance2.context"),
    outputSummary: t("topology.summary.finance2.output"),
    effectSummary: t("topology.summary.finance2.effect"),
  });
  if (toolActivity) {
    financeTwoNode.invocation = {
      kind: "Tool",
      name: toolActivity.tool_or_skill,
      status: toolActivity.status,
      receipt: toolActivity.receipt_digest,
    };
    financeTwoNode.tool = toolActivity.tool_or_skill;
  }
  const reviewerTwoNode = goldenTaskNode(reviewerAttemptTwo, runs, handoffs, {
    role: t("topology.role.reviewerA2"),
    status: t("topology.status.atReceivedDecision", {
      decision: displayToken(reviewer.attempt_2 && reviewer.attempt_2.verdict || "NOT_OBSERVED"),
    }),
    tone: reviewer.attempt_2 && reviewer.attempt_2.verdict === "PASS" ? "pass" : "not-run",
    taskSummary: t("topology.summary.review2.task"),
    contextSummary: t("topology.summary.review2.context"),
    outputSummary: t("topology.summary.review2.output"),
    reviewDecision: t("topology.summary.review2.decision"),
    effectSummary: t("topology.summary.review2.effect"),
  });
  const gtmComposeNode = skillActivity ? {
    ...goldenTaskNode(gtmTask, runs, handoffs, {
      role: t("topology.role.gtmCompose"),
      name: skillActivity.actor_or_domain,
      status: t("topology.status.skillComposed"),
      tone: skillReleaseReady ? "pass" : "not-run",
      taskSummary: t("topology.summary.gtm.task"),
      contextSummary: t("topology.summary.gtm.context"),
      outputSummary: t("topology.summary.gtm.output"),
      handoffTo: t("topology.role.control"),
      effectSummary: t("topology.summary.gtm.effect"),
    }),
    dependsOn: reviewerAttemptTwo && [reviewerAttemptTwo.id],
    inputRefs: [reviewerAttemptTwo && reviewerAttemptTwo.output_digest, tool.receipt_digest].filter(Boolean),
    inputDigest: reviewerAttemptTwo && reviewerAttemptTwo.output_digest,
    skill: skill.package_id,
    writes: skillActivity.target_writes,
    evidenceMode: skillActivity.evidence_class,
    invocation: {
      kind: "Skill",
      name: skillActivity.tool_or_skill,
      status: skillActivity.status,
      receipt: skillActivity.receipt_digest,
    },
  } : null;
  const formationNode = {
    actorKind: "control",
    role: t("topology.role.control"),
    name: t("topology.name.formation"),
    status: state.formation ? "VERIFIED · ATOMIC COMMIT" : "VERIFIED · WAITING COMMIT",
    tone: "control",
    dependsOn: [skillActivity && skillActivity.receipt_digest].filter(Boolean),
    inputRefs: [competition.summary_digest, skill.receipt_digest].filter(Boolean),
    inputDigest: competition.summary_digest,
    digest: competition.prepared_formation_digest,
    outputDigest: state.formation && state.formation.digest,
    model: t("topology.detail.failClosedVerifier"),
    tool: null,
    skill: null,
    trace: competition.summary_digest,
    candidateOnly: false,
    writes: state.formation ? "GOVERNED" : 0,
    evidenceMode: "CONTROLLED_LOCAL_AGENTTEAMS",
    taskSummary: t("topology.summary.control.task"),
    contextSummary: t("topology.summary.control.context"),
    outputSummary: t("topology.summary.control.output"),
    handoffTo: t("topology.summary.control.handoff"),
    effectSummary: t("topology.summary.control.effect"),
  };
  const humanNode = {
    actorKind: "human",
    role: t("topology.role.human"),
    name: approvalOwner || "exact-domain-owner",
    status: approval ? "EXPLICIT APPROVAL" : preview ? "WAITING FOR CLICK" : "NEXT HIGH-RISK CHANGE WAITS",
    tone: approval ? "human" : "not-run",
    dependsOn: preview ? ["deterministic-impact-preview"] : [],
    inputRefs: preview && [preview.preview_digest],
    inputDigest: preview && preview.preview_digest,
    digest: approvalDigest,
    outputDigest: approvalDigest,
    model: "NONE",
    tool: null,
    skill: null,
    trace: state.event_chain && state.event_chain.head_digest,
    candidateOnly: "N/A",
    writes: 0,
    evidenceMode: approval ? "LOCAL_EXPLICIT_WORKSPACE_APPROVAL" : "WAITING",
    taskSummary: t("topology.summary.human.task", { change: t("business.change.generic") }),
    contextSummary: t("topology.summary.human.context"),
    outputSummary: t("topology.summary.human.output"),
    handoffTo: t("topology.summary.human.handoff"),
    effectSummary: t("topology.summary.human.effect"),
  };
  const approvalHistory = approvalRecords(state);
  const humanNodes = state.business_complete === true && approvalHistory.length > 0
    ? approvalHistory.map((record, index) => {
      const exactApproval = record.approval || record;
      const digest = record.approval_digest || record.artifact_digest || exactApproval.digest;
      const actor = approvalActor(record) || "exact-domain-owner";
      const review = record.approval_review_evidence || {};
      const changeKind = record.kind || record.binding && record.binding.change_kind || "CHANGE";
      return {
        actorKind: "human",
        role: t("topology.role.human"),
        name: actor,
        status: t("topology.status.humanApprovalBound", {
          round: index + 1,
          change: displayToken(changeKind),
        }),
        tone: "human",
        dependsOn: exactApproval.preview_digest ? [exactApproval.preview_digest] : [],
        inputRefs: exactApproval.preview_digest ? [exactApproval.preview_digest] : [],
        inputDigest: exactApproval.preview_digest,
        digest,
        outputDigest: digest,
        model: "NONE",
        tool: null,
        skill: null,
        trace: review.event_digest || state.event_chain && state.event_chain.head_digest,
        candidateOnly: "N/A",
        writes: 0,
        evidenceMode: exactApproval.method || "LOCAL_EXPLICIT_WORKSPACE_APPROVAL",
        taskSummary: t("topology.summary.human.task", { change: displayToken(changeKind) }),
        contextSummary: `${t("topology.summary.human.context")} · ${t("topology.detail.serverWait", {
          milliseconds: review.review_duration_ms || 0,
        })}`,
        outputSummary: t("topology.summary.human.output"),
        handoffTo: t("topology.summary.human.handoff"),
        effectSummary: t("topology.summary.human.effect"),
      };
    })
    : [humanNode];
  const storeNode = {
    actorKind: "store",
    role: t("topology.role.canonical"),
    name: t("authority.canonical"),
    status: latestOutcome ? `${t("enum.governed")} · ${quoteRevisionLabel(quote.version)}` : quote.version ? `${quoteRevisionLabel(quote.version)} · ${t("enum.current")}` : "WAITING",
    tone: quote.version ? "store" : "not-run",
    dependsOn: approvalDigest ? [approvalOwner] : ["Controlled AgentTeams Formation"],
    inputRefs: [approvalDigest, competition.prepared_formation_digest].filter(Boolean),
    inputDigest: approvalDigest || competition.prepared_formation_digest,
    digest: latestOutcome && (latestOutcome.artifact_digest || latestOutcome.digest) || state.formation && state.formation.digest,
    outputDigest: quote.digest,
    model: t("topology.detail.onlyWriter"),
    tool: null,
    skill: null,
    trace: state.event_chain && state.event_chain.head_digest,
    candidateOnly: false,
    writes: quote.version ? "GOVERNED" : 0,
    evidenceMode: "DETERMINISTIC_CONTROL_PLANE",
    taskSummary: t("topology.summary.store.task"),
    contextSummary: t("topology.summary.store.context"),
    outputSummary: t("topology.summary.store.output", { quote: quoteRevisionLabel(quote.version) }),
    handoffTo: t("topology.summary.store.handoff"),
    effectSummary: t("topology.summary.store.effect"),
  };

  text("current-lane-title", t("collaboration.active.title"));
  text("current-lane-detail", `${t("collaboration.active.detail")} ${t("collaboration.active.model", {
    suggestion: reviewerSuggestion,
    provider: reviewerProvider,
    model: reviewerModel,
  })}`);
  exactAuditTitle("current-lane-detail");
  text("active-topology-boundary", t(oacExecutionBound ? "topology.boundary.activeOac" : "topology.boundary.active", {
    runId: competition.run_id,
    suggestion: reviewerSuggestion,
    provider: reviewerProvider,
    model: reviewerModel,
  }));
  exactAuditTitle("active-topology-boundary");
  byId("active-agent-topology").classList.remove("empty-topology");
  byId("active-agent-topology").classList.add("golden-topology");
  renderGoldenNativeLifecycle(competition, collaboration, tasks, runs, handoffs);
  renderAgentWorkObservability(state, competition, collaboration, tasks, runs, handoffs);
  renderDirectedTopology("active-agent-topology", [
    ...(oacFormationNode
      ? [{ label: t("topology.stage.oac"), className: "control-stage", nodes: [oacFormationNode] }]
      : []),
    { label: t("topology.stage.manager"), className: "leader-stage", nodes: [managerNode] },
    { label: t("topology.stage.domains"), className: "workers-stage", nodes: workerNodes },
    { label: t("topology.stage.recovery"), className: "control-stage", nodes: [reviewerOneNode, financeTwoNode, reviewerTwoNode] },
    { label: t("topology.stage.skill"), className: "control-stage", nodes: [...(gtmComposeNode ? [gtmComposeNode] : []), formationNode] },
    { label: t("topology.stage.human"), className: "human-stage", nodes: humanNodes },
    { label: t("topology.stage.canonical"), className: "store-stage", nodes: [storeNode] },
  ]);
  renderSelectiveChangeCard(state);
}

function renderActiveCollaboration(state) {
  const competition = activeCompetition(state);
  if (competition) {
    renderGoldenCollaboration(state, competition);
    return;
  }
  hideGoldenNativeLifecycle();
  hideAgentWorkObservability();
  byId("active-agent-topology").classList.remove("golden-topology");
  byId("active-agent-topology").classList.remove("empty-topology");
  const localCollaborationCopy = state.stage === "EMPTY"
    ? "collaboration.waiting"
    : currentRunValueProjection(state).status === "PASS"
      ? "collaboration.local.complete"
      : "collaboration.local.active";
  text("current-lane-title", t(`${localCollaborationCopy}.title`));
  text("current-lane-detail", t(`${localCollaborationCopy}.detail`));
  const coalitionReady = Boolean(state.formation || (state.coalition && state.coalition.plan_ref));
  const execution = stateExecution(state);
  const advisory = activeAdvisorySnapshot(state) || {};
  const plan = advisory.orchestration_plan || advisory.plan || {};
  const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
  const agentRuns = advisory && Array.isArray(advisory.agent_runs) ? advisory.agent_runs : [];
  const handoffs = advisory && Array.isArray(advisory.handoffs) ? advisory.handoffs : [];
  const teamObserved = coalitionReady || tasks.length > 0 || agentRuns.length > 0 || handoffs.length > 0;
  if (!teamObserved) {
    text("active-topology-boundary", t("collaboration.empty.detail"));
    byId("active-agent-topology").classList.add("empty-topology");
    byId("active-agent-topology").innerHTML = `<article class="topology-empty-state">
      <span>${escapeHtml(t("collaboration.empty.kicker"))}</span>
      <strong>${escapeHtml(t("collaboration.empty.title"))}</strong>
      <small>${escapeHtml(t("collaboration.empty.detail"))}</small>
    </article>`;
    renderSelectiveChangeCard(state);
    return;
  }
  const coordinatorTask = tasks.find((task) => task.agent_name === "change-coordinator" || task.authority_domain === "coordination") || null;
  const coordinatorRun = runForTask(agentRuns, coordinatorTask, "change-coordinator");
  const coordinatorHandoff = handoffForTask(handoffs, coordinatorTask, "change-coordinator");
  const workers = DOMAIN_WORKERS.map(({ domain, label, actor }) => {
    const task = taskForAgent(tasks, actor, domain);
    const run = runForTask(agentRuns, task, actor);
    const handoff = handoffForTask(handoffs, task, actor);
    const payload = (handoff && handoff.payload) || {};
    const hasRun = Boolean(run);
    return {
      actorKind: "agent",
      role: t("topology.role.workerGeneric", { domain: displayToken(label) }),
      name: (run && run.agent_name) || (task && task.agent_name) || actor,
      status: hasRun ? run.status : coalitionReady ? "CONTEXT ONLY · NO WORKER RUN OBSERVED" : "WAITING",
      tone: hasRun && run.status === "TRUSTED_COMPLETE" ? "pass" : "not-run",
      dependsOn: task && task.depends_on,
      inputRefs: (task && task.input_refs) || (handoff && handoff.input_refs),
      inputDigest: run && run.input_digest,
      digest: (task && task.digest) || (handoff && handoff.digest) || (run && run.digest),
      outputDigest: run && run.output_digest,
      model: run && run.model_version,
      tool: run && run.tool_versions,
      skill: run && run.skill_versions,
      trace: run && run.trace_id,
      candidateOnly: (handoff && handoff.candidate_only) ?? (task && task.candidate_only),
      writes: hasRun ? payload.target_writes ?? 0 : "NOT_OBSERVED",
      evidenceMode: run && run.evidence_class,
    };
  });
  const preview = activePreview(state);
  const approval = activeApproval(state);
  const approvalDigest = approval && (approval.approval_digest || approval.artifact_digest || (approval.approval && approval.approval.digest));
  const approvalOwner = approvalActor(approval) || (state.actions && state.actions.owner_id);
  const latestOutcome = state.latest_outcome || null;
  const quote = state.quote || {};
  const handoffDigests = handoffs.map((handoff) => handoff.digest).filter(Boolean);
  const controlNode = {
    actorKind: "control",
    role: t("topology.role.controlAcceptance"),
    name: "system:impact-control",
    status: preview ? "PREVIEW LOCKED" : "WAITING FOR CANDIDATES",
    tone: preview ? "control" : "not-run",
    dependsOn: tasks.filter((task) => task.authority_domain !== "coordination").map((task) => task.id),
    inputRefs: handoffDigests,
    inputDigest: plan.digest,
    digest: preview && preview.preview_digest,
    outputDigest: preview && preview.preview_digest,
    model: t("topology.detail.deterministic"),
    tool: state.dependency_evidence_tool && state.dependency_evidence_tool.status === "SUCCEEDED" ? "dependency-evidence@READ_ONLY" : "NOT_OBSERVED",
    skill: "NOT_OBSERVED",
    trace: "NOT_OBSERVED",
    candidateOnly: true,
    writes: 0,
    evidenceMode: execution.mode,
  };
  const humanNode = {
    actorKind: "human",
    role: t("topology.role.humanOwner"),
    name: approvalOwner || "exact-domain-owner",
    status: approval ? "EXPLICIT LOCAL APPROVAL" : "WAITING FOR HUMAN CLICK",
    tone: approval ? "human" : "not-run",
    dependsOn: preview ? ["system:impact-control"] : [],
    inputRefs: preview && preview.preview_digest ? [preview.preview_digest] : [],
    inputDigest: preview && preview.preview_digest,
    digest: approvalDigest,
    outputDigest: approvalDigest,
    model: "NONE",
    tool: t("topology.detail.ownerCommand"),
    skill: "NONE",
    trace: "NOT_OBSERVED",
    candidateOnly: "N/A",
    writes: 0,
    evidenceMode: approval ? "LOCAL_EXPLICIT_WORKSPACE_APPROVAL" : "NOT_OBSERVED",
  };
  const storeNode = {
    actorKind: "store",
    role: t("topology.role.canonicalState"),
    name: "StateStore / RebaseWorkflow",
    status: latestOutcome ? `${t("enum.governed")} · ${quoteRevisionLabel(quote.version)}` : quote.version ? `${quoteRevisionLabel(quote.version)} · ${t("enum.current")}` : "WAITING",
    tone: quote.version ? "store" : "not-run",
    dependsOn: approvalDigest ? [approvalOwner || "exact-domain-owner"] : [],
    inputRefs: approvalDigest ? [approvalDigest] : [],
    inputDigest: approvalDigest,
    digest: latestOutcome && (latestOutcome.artifact_digest || latestOutcome.digest),
    outputDigest: quote.digest,
    model: t("topology.detail.deterministic"),
    tool: "StateStore + RebaseWorkflow",
    skill: "NOT_OBSERVED",
    trace: state.event_chain && state.event_chain.head_digest,
    candidateOnly: false,
    writes: latestOutcome ? "GOVERNED_COUNT_NOT_OBSERVED" : quote.version ? "GOVERNED_FORMATION_COUNT_NOT_OBSERVED" : 0,
    evidenceMode: execution.canonical_authority,
  };
  const coordinatorNode = {
    actorKind: "agent",
    role: t("topology.role.coordinator"),
    name: (coordinatorRun && coordinatorRun.agent_name) || (coordinatorTask && coordinatorTask.agent_name) || "change-coordinator",
    status: coordinatorRun ? coordinatorRun.status : "NO COORDINATOR RUN OBSERVED",
    tone: coordinatorRun && coordinatorRun.status === "TRUSTED_COMPLETE" ? "pass" : "not-run",
    dependsOn: coordinatorTask && coordinatorTask.depends_on,
    inputRefs: (coordinatorTask && coordinatorTask.input_refs) || (coordinatorHandoff && coordinatorHandoff.input_refs),
    inputDigest: coordinatorRun && coordinatorRun.input_digest,
    digest: (coordinatorTask && coordinatorTask.digest) || (coordinatorHandoff && coordinatorHandoff.digest),
    outputDigest: coordinatorRun && coordinatorRun.output_digest,
    model: coordinatorRun && coordinatorRun.model_version,
    tool: coordinatorRun && coordinatorRun.tool_versions,
    skill: coordinatorRun && coordinatorRun.skill_versions,
    trace: coordinatorRun && coordinatorRun.trace_id,
    candidateOnly: (coordinatorHandoff && coordinatorHandoff.candidate_only) ?? (coordinatorTask && coordinatorTask.candidate_only),
    writes: coordinatorRun ? ((coordinatorHandoff && coordinatorHandoff.payload && coordinatorHandoff.payload.target_writes) ?? 0) : "NOT_OBSERVED",
    evidenceMode: coordinatorRun && coordinatorRun.evidence_class,
  };
  text("active-topology-boundary", t("topology.boundary.fallback", {
    runId: execution.run_id,
    mode: displayToken(execution.mode),
    agentteams: displayToken(execution.agentteams),
    runtime: displayToken(execution.candidate_runtime),
  }));
  exactAuditTitle("active-topology-boundary");
  renderDirectedTopology("active-agent-topology", [
    { label: t("topology.stage.coordinate"), className: "leader-stage", nodes: [coordinatorNode] },
    { label: t("topology.stage.domainWorkers"), className: "workers-stage", nodes: workers },
    { label: t("topology.stage.accept"), className: "control-stage", nodes: [controlNode] },
    { label: t("topology.stage.humanShort"), className: "human-stage", nodes: [humanNode] },
    { label: t("topology.stage.write"), className: "store-stage", nodes: [storeNode] },
  ]);
  renderSelectiveChangeCard(state);
}

function experienceRemaining(experience) {
  if (!experience || experience.status !== "AWAITING_HUMAN_APPROVAL") return 0;
  const gate = experience.review_gate || {};
  const notBefore = Number(gate.not_before_epoch_ms);
  if (Number.isFinite(notBefore)) return Math.max(0, notBefore - Date.now());
  return Math.max(0, Number(experience.review_remaining_ms) || 0);
}

function renderExperience(state) {
  const panel = byId("experience-governance");
  const experience = state.experience_governance || null;
  const visible = state.business_complete === true
    && experience
    && !["NOT_RUN", "WAITING_FOR_PENDING_CHANGES"].includes(experience.status);
  panel.hidden = !visible;
  clearTimeout(experienceTimer);
  experienceTimer = null;
  if (!visible) return;

  const candidate = experience.candidate || {};
  const evaluation = experience.evaluation || {};
  const release = experience.release || {};
  const status = experience.status;
  const statusKey = {
    AWAITING_HUMAN_APPROVAL: "experience.status.awaiting",
    APPROVED_CANARY: "experience.status.approved",
    REJECTED: "experience.status.rejected",
    NO_CANDIDATE: "experience.status.none",
  }[status] || "experience.status.none";
  text("experience-status", t(statusKey));
  byId("experience-source").innerHTML = `<span class="localized-fact"><span class="machine-token">${escapeHtml(shortDigest(experience.run_id, 28))}</span>${localizedFactHtml(candidate.maturity || candidate.outcome || "NOT_OBSERVED")}</span>`;
  text("experience-pattern", candidate.outcome === "IMPROVE" ? t("experience.pattern") : (candidate.reason_codes || []).join(" · "));
  byId("experience-evaluation").innerHTML = evaluation.verdict
    ? `${localizedFactHtml(evaluation.verdict)} <span class="machine-token">${escapeHtml((evaluation.case_results || []).length)}/8</span>`
    : localizedFactHtml("NOT_RUN");
  text("experience-authority", experience.discoverable
    ? t("experience.authority.open")
    : t("experience.authority.locked"));
  localizedBusinessText("experience-owner", experience.owner_id || "human:skill-steward");

  if (status === "APPROVED_CANARY") {
    byId("experience-release").innerHTML = `${localizedFactHtml(release.release_state || "CANARY")} <span class="machine-token">${escapeHtml(shortDigest(release.release_head_digest, 16))}</span>`;
    text("experience-release-detail", t("experience.release.canary"));
    text("experience-review-note", t("experience.review.approved"));
  } else if (status === "REJECTED") {
    text("experience-release", t("experience.release.rejected"));
    text("experience-release-detail", shortDigest(experience.decision && experience.decision.digest, 22));
    text("experience-review-note", t("experience.review.rejected"));
  } else if (status === "NO_CANDIDATE") {
    localizedText("experience-release", "NO_CANDIDATE");
    text("experience-release-detail", t("experience.release.pending"));
    text("experience-review-note", t("experience.review.none"));
  } else {
    text("experience-release", t("experience.release.pending"));
    text("experience-release-detail", shortDigest(candidate.digest, 22));
  }

  const remaining = experienceRemaining(experience);
  const awaiting = status === "AWAITING_HUMAN_APPROVAL";
  const approve = byId("experience-approve");
  const reject = byId("experience-reject");
  const session = window.OrgRebaseClient.session();
  const ownerAllowed = !session?.authentication_required || session.authenticated
    && session.principal?.actor_id === (experience.owner_id || "human:skill-steward");
  approve.disabled = experienceInFlight || !awaiting || !ownerAllowed || remaining > 0 || experience.head_fresh === false;
  reject.disabled = experienceInFlight || !awaiting || !ownerAllowed || remaining > 0;
  approve.textContent = awaiting && remaining > 0
    ? t("experience.button.review", { seconds: Math.ceil(remaining / 1000) })
    : t("experience.approve");
  reject.textContent = t("experience.reject");
  if (awaiting) {
    text(
      "experience-review-note",
      remaining > 0
        ? t("experience.review.wait", { seconds: Math.ceil(remaining / 1000) })
        : t("experience.review.ready"),
    );
    if (remaining > 0) {
      experienceTimer = setTimeout(() => {
        if (currentState) renderExperience(currentState);
      }, Math.min(250, remaining));
    }
  }
}

function renderCapabilityCenter(state) {
  const root = byId("skill-current-invocation");
  if (!root) return;
  const competition = activeCompetition(state);
  const collaboration = competition && competition.agent_collaboration || {};
  const skill = collaboration.skill || {};
  const tasks = Array.isArray(collaboration.orchestration_plan && collaboration.orchestration_plan.tasks)
    ? collaboration.orchestration_plan.tasks
    : [];
  const rows = Array.isArray(state && state.execution_activity && state.execution_activity.rows)
    ? state.execution_activity.rows
    : [];
  const gtmTask = tasks.find((task) => (
    task.role === "DOMAIN_WORKER"
      && task.authority_domain === "gtm"
      && task.attempt === 1
  )) || null;
  const invocation = competition
    ? exactGtmSkillInvocation(rows, competition.run_id, skill, gtmTask)
    : null;
  if (!invocation) {
    root.dataset.state = "waiting";
    text("skill-current-agent", "—");
    text("skill-current-name", "—");
    text("skill-current-purpose", t("capability.current.waiting"));
    text("skill-current-effect", "—");
    text("skill-current-receipt", "");
    byId("skill-current-receipt").removeAttribute("title");
    return;
  }
  const packageToken = String(invocation.tool_or_skill || "");
  const versionMatch = packageToken.match(/@([^@]+)$/);
  root.dataset.state = "observed";
  localizedBusinessText("skill-current-agent", invocation.actor_or_domain);
  text("skill-current-name", t("capability.current.package", {
    version: versionMatch ? versionMatch[1] : displayToken("NOT_OBSERVED"),
  }));
  text("skill-current-purpose", t("capability.current.quoteCompose"));
  text("skill-current-effect", t("capability.current.success"));
  text("skill-current-receipt", t("capability.current.receipt"));
  exactAuditTitle("skill-current-receipt", invocation.receipt_digest, competition.run_id);
}

const SKILL_DISPLAY_NAME_KEYS = Object.freeze({
  "enterprise-quote-compose": "skill.display.enterpriseQuoteCompose",
  "structured-domain-handoff": "skill.display.structuredDomainHandoff",
  "enterprise-launch-readiness": "skill.display.enterpriseLaunchReadiness",
});

function skillDisplayName(name) {
  const key = SKILL_DISPLAY_NAME_KEYS[String(name || "")];
  return key ? t(key) : String(name || "");
}

function normalizeSkillSourceCatalog(payload) {
  if (!payload || payload.status !== "PASS" || payload.schema_version !== "orgrebase.skill-source-catalog.v1") {
    return { status: "UNAVAILABLE", packages: [] };
  }
  if (!Array.isArray(payload.packages)) return { status: "UNAVAILABLE", packages: [] };
  const packages = payload.packages.filter((skillPackage) => (
    skillPackage
      && typeof skillPackage.name === "string"
      && typeof skillPackage.version === "string"
      && typeof skillPackage.package_digest === "string"
      && typeof skillPackage.skill_digest === "string"
      && typeof skillPackage.content === "string"
      && Number.isInteger(skillPackage.content_bytes)
      && skillPackage.release_state === "PUBLISHED_IMMUTABLE"
      && skillPackage.editable === false
  )).map((skillPackage) => ({
    ...skillPackage,
    drafts: Array.isArray(skillPackage.drafts) ? skillPackage.drafts.filter((draft) => (
      draft
        && draft.status === "DRAFT_SAVED"
        && draft.candidate_only === true
        && draft.evaluation_status === "NOT_EVALUATED"
        && draft.release_status === "NOT_RELEASED"
        && draft.executable === false
        && draft.registry_writes === 0
        && draft.canonical_target_writes === 0
    )) : [],
  }));
  if (!packages.length) return { status: "UNAVAILABLE", packages: [] };
  return { ...payload, status: "PASS", packages };
}

function skillSourceElement(tagName, className, content) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = String(content);
  return node;
}

function selectedSkillSource() {
  const packages = currentSkillCatalog && currentSkillCatalog.packages;
  return Array.isArray(packages)
    ? packages.find((skillPackage) => skillPackage.name === selectedSkillName) || null
    : null;
}

function renderSkillSourceCatalog() {
  const root = byId("skill-source-catalog");
  if (!root) return;
  root.replaceChildren();
  if (!currentSkillCatalog) {
    root.dataset.state = "loading";
    root.append(skillSourceElement("p", "skill-source-empty", t("skill.source.catalogLoading")));
    return;
  }
  if (currentSkillCatalog.status !== "PASS") {
    root.dataset.state = "unavailable";
    root.append(skillSourceElement("p", "skill-source-empty", t("skill.source.catalogUnavailable")));
    return;
  }
  root.dataset.state = "ready";
  const header = skillSourceElement("div", "skill-source-catalog-header");
  header.append(
    skillSourceElement("strong", "", t("skill.source.catalogTitle")),
    skillSourceElement("small", "", t("skill.source.catalogBoundary")),
  );
  const grid = skillSourceElement("div", "skill-source-card-grid");
  currentSkillCatalog.packages.forEach((skillPackage) => {
    const card = skillSourceElement("article", "skill-source-card");
    card.append(
      skillSourceElement("span", "", t("skill.source.publishedImmutable")),
      skillSourceElement("strong", "", skillDisplayName(skillPackage.name)),
    );
    const facts = skillSourceElement("div", "skill-source-card-facts");
    facts.append(
      skillSourceElement("small", "skill-stable-id", t("skill.display.stableId", { id: skillPackage.name })),
      skillSourceElement("small", "", skillPackage.version),
      skillSourceElement("small", "", t("skill.source.bytes", { count: skillPackage.content_bytes })),
      skillSourceElement("small", "", t("skill.source.draftCountValue", { count: skillPackage.drafts.length })),
    );
    const openButton = skillSourceElement("button", "button button-secondary", t("skill.source.open"));
    openButton.type = "button";
    openButton.dataset.skillSourceOpen = skillPackage.name;
    card.append(facts, openButton);
    grid.append(card);
  });
  root.append(header, grid);
}

function renderSkillDraftList(skillPackage) {
  const root = byId("skill-source-draft-list");
  root.replaceChildren();
  const drafts = Array.isArray(skillPackage && skillPackage.drafts) ? skillPackage.drafts : [];
  if (!drafts.length) {
    root.append(skillSourceElement("p", "skill-source-empty", t("skill.source.noDrafts")));
    return;
  }
  drafts.forEach((draft) => {
    const row = skillSourceElement("article", "skill-source-draft-row");
    row.append(
      skillSourceElement("strong", "", t("skill.source.draftReceipt", {
        version: draft.proposed_version || "—",
      })),
      skillSourceElement("small", "", t("skill.source.draftOwner")),
    );
    const governanceFacts = skillSourceElement("div", "skill-source-draft-facts");
    governanceFacts.append(
      skillSourceElement("code", "", draft.evaluation_status),
      skillSourceElement("code", "", draft.release_status),
      skillSourceElement("code", "", `executable=${String(draft.executable)}`),
      skillSourceElement("code", "", `registry writes=${draft.registry_writes}`),
    );
    row.append(governanceFacts);
    const exactReceipt = [draft.artifact_id, draft.artifact_payload_digest, draft.draft_skill_digest]
      .filter(Boolean)
      .join(" · ");
    if (exactReceipt) row.title = exactReceipt;
    root.append(row);
  });
}

function renderSkillDialogDynamicCopy() {
  const dialog = byId("skill-source-dialog");
  if (!dialog || !dialog.open) return;
  const skillPackage = selectedSkillSource();
  if (!skillPackage) return;
  text("skill-source-dialog-title", skillDisplayName(skillPackage.name));
  byId("skill-source-dialog-title").title = t("skill.display.stableId", { id: skillPackage.name });
  text("skill-source-version", skillPackage.version);
  text("skill-source-state", t("skill.source.publishedImmutable"));
  text("skill-source-draft-count", t("skill.source.draftCountValue", { count: skillPackage.drafts.length }));
  text("skill-source-editor-label", t(skillDraftEditing ? "skill.source.draftText" : "skill.source.publishedText"));
  const status = byId("skill-source-status");
  status.textContent = t(skillDialogStatus.key, skillDialogStatus.params);
  status.dataset.tone = skillDialogStatus.tone;
  renderSkillDraftList(skillPackage);
}

function resetSkillSourceDialogToPublished(skillPackage, { preserveStatus = false } = {}) {
  skillDraftEditing = false;
  const editor = byId("skill-source-editor");
  editor.value = skillPackage.content;
  editor.readOnly = true;
  editor.scrollTop = 0;
  byId("skill-source-proposed-version").value = "";
  byId("skill-source-version-field").hidden = true;
  byId("skill-source-cancel").hidden = true;
  byId("skill-source-save").hidden = true;
  byId("skill-source-revise").hidden = false;
  byId("skill-source-revise").disabled = !canReviseSkill();
  byId("skill-source-revise").title = canReviseSkill() ? "" : t("skill.source.draftOwner");
  if (!preserveStatus) {
    skillDialogStatus = { key: "skill.source.readonlyStatus", params: {}, tone: "neutral" };
  }
  renderSkillDialogDynamicCopy();
}

function openSkillSourceDialog(name) {
  selectedSkillName = name;
  const skillPackage = selectedSkillSource();
  if (!skillPackage) return;
  const dialog = byId("skill-source-dialog");
  text("skill-source-package-digest", skillPackage.package_digest);
  text("skill-source-text-digest", skillPackage.skill_digest);
  if (!dialog.open) dialog.showModal();
  resetSkillSourceDialogToPublished(skillPackage);
}

function nextSkillPatchVersion(version) {
  const match = String(version || "").match(/^(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?$/);
  if (!match) return "";
  return `${match[1]}.${match[2]}.${Number(match[3]) + 1}`;
}

function replaceSkillFrontmatterVersion(content, version) {
  const newline = String(content).includes("\r\n") ? "\r\n" : "\n";
  const lines = String(content).split(/\r?\n/);
  if (lines[0] !== "---") return content;
  const closing = lines.indexOf("---", 1);
  if (closing < 0) return content;

  const replaceAt = (index) => {
    const match = lines[index].match(/^(\s*version:\s*)(.*?)(\s+#.*)?$/);
    if (!match) return false;
    const rawValue = match[2].trim();
    const quote = rawValue.startsWith('"') && rawValue.endsWith('"')
      ? '"'
      : rawValue.startsWith("'") && rawValue.endsWith("'")
        ? "'"
        : "";
    lines[index] = `${match[1]}${quote}${version}${quote}${match[3] || ""}`;
    return true;
  };

  const metadataIndex = lines.slice(1, closing).findIndex((line) => /^metadata:\s*(?:#.*)?$/.test(line));
  if (metadataIndex >= 0) {
    const absoluteMetadataIndex = metadataIndex + 1;
    for (let index = absoluteMetadataIndex + 1; index < closing; index += 1) {
      const line = lines[index];
      if (line.trim() && !/^\s/.test(line)) break;
      if (/^\s+version:\s*/.test(line) && replaceAt(index)) return lines.join(newline);
    }
  }

  // Keep older published packages editable during migration, while canonical
  // skill-creator packages use metadata.version above.
  const legacyIndex = lines.slice(1, closing).findIndex((line) => /^version:\s*/.test(line));
  if (legacyIndex < 0 || !replaceAt(legacyIndex + 1)) return content;
  return lines.join(newline);
}

function canReviseSkill() {
  const session = window.OrgRebaseClient.session();
  return Boolean(session && (!session.authentication_required && session.mode === "local"
    || session.authenticated && session.principal?.actor_id === "human:skill-steward"
      && session.principal.roles?.some(role => ["governor", "administrator"].includes(role))));
}

function beginSkillRevision() {
  const skillPackage = selectedSkillSource();
  if (!skillPackage || skillDraftInFlight || !canReviseSkill()) return;
  const proposedVersion = nextSkillPatchVersion(skillPackage.version);
  skillDraftEditing = true;
  byId("skill-source-proposed-version").value = proposedVersion;
  const editor = byId("skill-source-editor");
  editor.value = replaceSkillFrontmatterVersion(skillPackage.content, proposedVersion);
  editor.readOnly = false;
  editor.scrollTop = 0;
  byId("skill-source-version-field").hidden = false;
  byId("skill-source-cancel").hidden = false;
  byId("skill-source-save").hidden = false;
  byId("skill-source-revise").hidden = true;
  skillDialogStatus = { key: "skill.source.editingStatus", params: {}, tone: "neutral" };
  renderSkillDialogDynamicCopy();
  editor.focus();
}

function syncSkillDraftVersion() {
  if (!skillDraftEditing) return;
  const proposedVersion = byId("skill-source-proposed-version").value.trim();
  if (!proposedVersion) return;
  const editor = byId("skill-source-editor");
  editor.value = replaceSkillFrontmatterVersion(editor.value, proposedVersion);
}

function setSkillDraftControlsDisabled(disabled) {
  ["skill-source-proposed-version", "skill-source-editor", "skill-source-cancel", "skill-source-save"]
    .forEach((id) => { byId(id).disabled = disabled; });
}

function invalidateSkillSource() {
  ++skillSourceRevision;
  currentSkillCatalog = { status: "UNAVAILABLE", packages: [] };
  selectedSkillName = null;
  skillDraftEditing = false;
  skillDraftInFlight = false;
  skillDialogStatus = { key: "skill.source.readonlyStatus", params: {}, tone: "neutral" };
  const dialog = byId("skill-source-dialog");
  if (dialog.open) dialog.close();
  byId("skill-source-editor").value = "";
  byId("skill-source-editor").readOnly = true;
  byId("skill-source-proposed-version").value = "";
  byId("skill-source-draft-list").replaceChildren();
  ["skill-source-dialog-title", "skill-source-version", "skill-source-draft-count",
    "skill-source-package-digest", "skill-source-text-digest", "skill-source-status"].forEach((id) => {
    text(id, "");
    byId(id).removeAttribute("title");
  });
  setSkillDraftControlsDisabled(false);
  renderSkillSourceCatalog();
}

async function saveSkillRevisionDraft() {
  const skillPackage = selectedSkillSource();
  if (!skillPackage || !skillDraftEditing || skillDraftInFlight || !canReviseSkill()) return;
  const proposedVersion = byId("skill-source-proposed-version").value.trim();
  if (!/^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/.test(proposedVersion)) {
    skillDialogStatus = { key: "skill.source.errorStatus", params: { message: t("skill.source.invalidVersion") }, tone: "error" };
    renderSkillDialogDynamicCopy();
    return;
  }
  syncSkillDraftVersion();
  const revision = skillSourceRevision;
  skillDraftInFlight = true;
  setSkillDraftControlsDisabled(true);
  try {
    const receipt = await api(`/api/workspace/skills/${encodeURIComponent(skillPackage.name)}/drafts`, {
      method: "POST",
      headers: { "X-OrgRebase-Actor": "human:skill-steward" },
      body: {
        actor_id: window.OrgRebaseClient.session()?.principal?.actor_id || "human:skill-steward",
        source_package_digest: skillPackage.package_digest,
        source_skill_digest: skillPackage.skill_digest,
        proposed_version: proposedVersion,
        content: byId("skill-source-editor").value,
      },
    });
    if (revision !== skillSourceRevision) return;
    const refreshed = normalizeSkillSourceCatalog(await api("/api/workspace/skills"));
    if (revision !== skillSourceRevision) return;
    currentSkillCatalog = refreshed;
    renderSkillSourceCatalog();
    const refreshedPackage = selectedSkillSource();
    skillDialogStatus = {
      key: "skill.source.savedStatus",
      params: { version: receipt.proposed_version || proposedVersion },
      tone: "success",
    };
    if (refreshedPackage) resetSkillSourceDialogToPublished(refreshedPackage, { preserveStatus: true });
    toast(t("skill.source.savedStatus", { version: receipt.proposed_version || proposedVersion }), "success");
  } catch (error) {
    if (revision !== skillSourceRevision) return;
    skillDialogStatus = { key: "skill.source.errorStatus", params: { message: error.message }, tone: "error" };
    renderSkillDialogDynamicCopy();
  } finally {
    if (revision === skillSourceRevision) {
      skillDraftInFlight = false;
      setSkillDraftControlsDisabled(false);
    }
  }
}

async function loadSkillSourceCatalog() {
  const revision = ++skillSourceRevision;
  try {
    const catalog = normalizeSkillSourceCatalog(await api("/api/workspace/skills"));
    if (revision !== skillSourceRevision) return;
    currentSkillCatalog = catalog;
  } catch (_) {
    if (revision !== skillSourceRevision) return;
    currentSkillCatalog = { status: "UNAVAILABLE", packages: [] };
  }
  renderSkillSourceCatalog();
}

async function decideExperience(decision) {
  const experience = currentState && currentState.experience_governance;
  if (experienceInFlight || !experience || experience.status !== "AWAITING_HUMAN_APPROVAL") return;
  const remaining = experienceRemaining(experience);
  if (remaining > 0) {
    toast(t("experience.toast.wait", { seconds: Math.ceil(remaining / 1000) }));
    return;
  }
  const candidate = experience.candidate || {};
  const evaluation = experience.evaluation || {};
  const approvalControl = currentState.approval_control || {};
  const owner = window.OrgRebaseClient.session()?.principal?.actor_id
    || experience.owner_id || "human:skill-steward";
  const headers = approvalControl.identity_mode === "CONTROLLED_LOCAL_HEADER_IDENTITY"
    ? { "X-OrgRebase-Actor": owner }
    : {};
  experienceInFlight = true;
  renderExperience(currentState);
  try {
    await api(`/api/workspace/experience/${decision.toLowerCase()}`, {
      method: "POST",
      headers,
      body: {
        run_id: experience.run_id,
        actor_id: owner,
        candidate_digest: candidate.digest,
        evaluation_digest: evaluation.digest,
        observed_skill_head_digest: experience.current_skill_head_digest,
      },
    });
    await refreshState();
    toast(
      decision === "APPROVE"
        ? t("experience.toast.approved")
        : t("experience.toast.rejected"),
      "success",
    );
  } catch (error) {
    toast(error.message, "error");
    try {
      await refreshState();
    } catch (_) {
      // Keep the last verified state if a refresh fails.
    }
  } finally {
    experienceInFlight = false;
    if (currentState) renderExperience(currentState);
    else renderWorkspaceUnavailable();
  }
}

function receiptForChange(change) {
  const envelope = change && change.outcome;
  const outcome = envelope && (envelope.outcome || envelope);
  return (outcome && (outcome.rebase_receipt || outcome.receipt)) || null;
}

function countOrObserved(value, fallback) {
  if (Array.isArray(value)) return value.length;
  if (value !== null && value !== undefined) return value;
  return fallback !== null && fallback !== undefined ? fallback : "NOT_OBSERVED";
}

const CURRENT_CHANGE_TEAM_CONTRACT = Object.freeze({
  launch_date: Object.freeze(["change-coordinator", "product-steward", "gtm-steward"]),
  currency: Object.freeze(["change-coordinator", "finance-steward", "gtm-steward"]),
});

const CURRENT_CHANGE_OWNER_CONTRACT = Object.freeze({
  launch_date: "human:evergreen-product-owner",
  currency: "human:evergreen-finance-owner",
});

const CURRENT_CHANGE_OBJECT_CONTRACT = Object.freeze({
  launch_date: "claim:product.launch_date",
  currency: "policy:finance.currency",
});

function sameOrderedEvidence(left, right) {
  return Boolean(
    Array.isArray(left)
    && Array.isArray(right)
    && left.length > 0
    && left.length === right.length
    && left.every((value, index) => value && value === right[index]),
  );
}

function changeProjection(kind, change, expectedRunId = null) {
  const previewEnvelope = change && change.preview;
  const bundle = previewEnvelope && (previewEnvelope.bundle || previewEnvelope);
  const changeSet = bundle && bundle.change_set;
  const changeSpec = bundle && bundle.change_spec;
  const impactPreview = bundle && (bundle.preview || bundle.impact_preview);
  const delta = changeSet && Array.isArray(changeSet.deltas) ? changeSet.deltas[0] : null;
  const previewDigest = previewEnvelope && previewEnvelope.preview_digest;
  const hasPreview = Boolean(previewEnvelope && bundle && (previewDigest || changeSet || changeSpec));
  const runEnvelope = bundle && bundle.run_envelope || {};
  const runNonce = runEnvelope.nonce;
  const advisory = bundle && bundle.advisory;
  const agentRuns = advisory && Array.isArray(advisory.agent_runs) ? advisory.agent_runs : [];
  const orchestrationPlan = advisory && advisory.orchestration_plan || {};
  const planTasks = Array.isArray(orchestrationPlan.tasks) ? orchestrationPlan.tasks : [];
  const handoffs = advisory && Array.isArray(advisory.handoffs) ? advisory.handoffs : [];
  const compilationReceipt = advisory && advisory.compilation_receipt || {};
  const coordinationReceipt = advisory && advisory.coordination_receipt || {};
  const ingestionReceipt = advisory && advisory.ingestion_receipt || {};
  const ingestionDecisions = Array.isArray(ingestionReceipt.decisions) ? ingestionReceipt.decisions : [];
  const agentEvidence = agentRuns.map((run) => {
    const task = planTasks.find((item) => item && (item.id === run.task_id || item.agent_name === run.agent_name)) || {};
    const handoff = handoffs.find((item) => item && (item.task_id === run.task_id || item.from_agent === run.agent_name)) || {};
    const decision = ingestionDecisions.find((item) => item && (
      item.artifact_ref === handoff.id || item.producer_worker === run.agent_name
    )) || {};
    const handoffPayload = handoff.payload || {};
    const changeObjectIds = Object.hasOwn(handoffPayload, "change_object_ids")
      ? handoffPayload.change_object_ids : [handoffPayload.change_object_id];
    const allowedOutputKinds = Array.isArray(task.allowed_output_kinds) ? task.allowed_output_kinds : [];
    return {
      actor: run.agent_name,
      status: run.status,
      evidenceClass: run.evidence_class,
      taskPurpose: task.purpose || null,
      contextScope: Array.isArray(task.context_scope) ? task.context_scope : [],
      changeObjectId: Array.isArray(changeObjectIds) && changeObjectIds.length === 1 ? changeObjectIds[0] : null,
      candidateKind: handoffPayload.kind || allowedOutputKinds[0] || null,
      candidateDigest: handoff.digest || decision.artifact_digest || null,
      agentOutputDigest: run.output_digest || null,
      tools: Array.isArray(run.tool_versions) ? run.tool_versions : [],
      skills: Array.isArray(run.skill_versions) ? run.skill_versions : [],
      handoffFrom: handoff.from_agent || run.agent_name,
      handoffTo: handoff.to_agent || null,
      handoffDigest: handoff.digest || null,
      reviewDecision: decision.decision || null,
      verifierEvidenceClass: ingestionReceipt.verifier_evidence_class || null,
      candidateOnly: handoff.candidate_only === true || task.candidate_only === true,
      targetWrites: Number.isFinite(Number(handoffPayload.target_writes))
        ? Number(handoffPayload.target_writes)
        : Number.isFinite(Number(ingestionReceipt.target_writes))
          ? Number(ingestionReceipt.target_writes)
          : null,
      bindingValid: Boolean(
        expectedRunId
        && runNonce
        && task.id === run.task_id
        && task.agent_name === run.agent_name
        && task.candidate_only === true
        && task.digest === handoff.delegation_task_digest
        && run.workflow_run_id === expectedRunId
        && run.run_nonce === runNonce
        && run.status === "TRUSTED_COMPLETE"
        && run.output_digest
        && handoff.task_id === run.task_id
        && handoff.from_agent === run.agent_name
        && handoff.to_agent === "orgrebase-control-plane"
        && handoff.workflow_run_id === expectedRunId
        && handoff.run_nonce === runNonce
        && handoff.candidate_only === true
        && handoff.orchestration_plan_digest === orchestrationPlan.digest
        && handoffPayload.candidate_only === true
        && handoffPayload.target_writes === 0
        && handoffPayload.preview_digest === previewDigest
        && sameOrderedEvidence(changeObjectIds, [changeSpec.object_id])
        && allowedOutputKinds.includes(handoffPayload.kind)
        && decision.producer_worker === run.agent_name
        && decision.artifact_ref === handoff.id
        && decision.artifact_digest === handoff.digest
        && decision.decision === "ADVISORY_ACCEPTED"
        && Array.isArray(decision.admitted_effects)
        && decision.admitted_effects.length === 0
      ),
    };
  });
  const agents = agentRuns.map((run) => run.agent_name).filter(Boolean);
  const expectedAgents = CURRENT_CHANGE_TEAM_CONTRACT[kind] || [];
  const expectedOwner = CURRENT_CHANGE_OWNER_CONTRACT[kind];
  const expectedObject = CURRENT_CHANGE_OBJECT_CONTRACT[kind];
  const agentRunDigests = agentRuns.map((run) => run && run.digest).filter(Boolean);
  const handoffDigests = handoffs.map((handoff) => handoff && handoff.digest).filter(Boolean);
  const compilationAgents = Array.isArray(compilationReceipt.identity_digests)
    ? compilationReceipt.identity_digests.map((identity) => identity && identity.name).filter(Boolean)
    : [];
  const changeEvidenceValid = Boolean(
    expectedRunId
    && previewDigest
    && runNonce
    && delta
    && changeSet && changeSpec
    && changeSet.id === changeSpec.id
    && changeSet.owner_id === changeSpec.owner_id
    && changeSpec.owner_id === expectedOwner
    && changeSpec.object_id === expectedObject
    && runEnvelope.run_id === expectedRunId
    && sameOrderedEvidence(agents, expectedAgents)
    && sameOrderedEvidence(planTasks.map((task) => task && task.agent_name), expectedAgents)
    && sameOrderedEvidence(compilationAgents, expectedAgents)
    && planTasks.length === expectedAgents.length
    && handoffs.length === expectedAgents.length
    && ingestionDecisions.length === expectedAgents.length
    && agentEvidence.length === expectedAgents.length
    && agentEvidence.every((item) => item.bindingValid && item.targetWrites === 0)
    && orchestrationPlan.preview_digest === previewDigest
    && orchestrationPlan.change_set_digest === changeSet.digest
    && compilationReceipt.preview_digest === previewDigest
    && compilationReceipt.change_set_digest === changeSet.digest
    && compilationReceipt.orchestration_plan_digest === orchestrationPlan.digest
    && coordinationReceipt.workflow_run_id === expectedRunId
    && coordinationReceipt.run_nonce === runNonce
    && coordinationReceipt.orchestration_plan_digest === orchestrationPlan.digest
    && sameOrderedEvidence(coordinationReceipt.agent_run_digests, agentRunDigests)
    && sameOrderedEvidence(coordinationReceipt.handoff_digests, handoffDigests)
    && coordinationReceipt.status === "PASS"
    && ingestionReceipt.run_id === expectedRunId
    && ingestionReceipt.nonce === runNonce
    && ingestionReceipt.preview_digest === previewDigest
    && ingestionReceipt.change_set_digest === changeSet.digest
    && ingestionReceipt.orchestration_plan_digest === orchestrationPlan.digest
    && ingestionReceipt.live_receipt_digest === coordinationReceipt.digest
    && ingestionReceipt.target_writes === 0
    && sameOrderedEvidence(ingestionReceipt.admitted_candidate_digests, handoffDigests)
    && Array.isArray(ingestionReceipt.rejected_candidate_digests)
    && ingestionReceipt.rejected_candidate_digests.length === 0
  );
  const approvalEnvelope = change && change.approval;
  const approval = approvalEnvelope && (approvalEnvelope.approval || approvalEnvelope);
  const reviewEvidence = approvalEnvelope && approvalEnvelope.approval_review_evidence;
  const receipt = receiptForChange(change);
  const receiptComplete = Boolean(receipt && receipt.status === "COMPLETED");
  const metrics = (receipt && receipt.metrics) || {};
  const previewCertificate = changeEvidenceValid ? exactVmrcBinding(bundle) : null;
  const previewResults = previewCertificate && impactPreview && Array.isArray(impactPreview.results)
    ? impactPreview.results
    : [];
  const candidateRebuild = previewResults.filter((item) => item && item.classification === "AFFECTED_HARD");
  const candidatePreserve = previewResults.filter(
    (item) => item && item.classification === "UNAFFECTED_WITHIN_DECLARED_BOUNDARY",
  );
  const candidateReview = previewResults.filter(
    (item) => item && ["UNKNOWN", "AFFECTED_REVIEW", "AFFECTED_INFORMATIONAL"].includes(item.classification),
  );
  const previewImpactValid = Boolean(previewCertificate && previewResults.length);
  const outcomeEnvelope = change && change.outcome;
  const outcome = outcomeEnvelope && (outcomeEnvelope.outcome || outcomeEnvelope);
  return {
    kind,
    hasPreview,
    delta,
    agents,
    agentEvidence,
    candidateTargetWrites: changeEvidenceValid ? 0 : null,
    changeEvidenceValid,
    workflowRunId: changeEvidenceValid ? expectedRunId : null,
    coordinationStatus: coordinationReceipt.status || null,
    owner: changeSpec && changeSpec.owner_id || null,
    approvalDigest: approvalEnvelope && (approvalEnvelope.approval_digest || approvalEnvelope.artifact_digest || approval && approval.digest),
    reviewWaitSatisfied: reviewEvidence && reviewEvidence.review_wait_satisfied === true,
    receipt,
    receiptComplete,
    previewImpactValid,
    impactPhase: receiptComplete ? "APPLIED" : previewImpactValid ? "PREVIEW" : "PENDING",
    appliedObjects: receipt && Array.isArray(receipt.applied_claims) ? receipt.applied_claims : candidateRebuild,
    preservedObjects: receipt && Array.isArray(receipt.bounded_unaffected) ? receipt.bounded_unaffected : candidatePreserve,
    reviewObjects: receipt && Array.isArray(receipt.unknown) ? receipt.unknown : candidateReview,
    metrics: {
      rebuilt: countOrObserved(!receiptComplete && previewImpactValid ? candidateRebuild.length : null, receipt && metrics.work_items_rebased),
      preserved: countOrObserved(!receiptComplete && previewImpactValid ? candidatePreserve.length : null, receipt && metrics.bounded_unaffected),
      unknown: countOrObserved(!receiptComplete && previewImpactValid ? candidateReview.length : null, receipt && metrics.unknown),
      falseInvalidations: countOrObserved(null, receipt && metrics.false_invalidations),
      unauthorized: countOrObserved(null, receipt && metrics.unauthorized_disclosures),
    },
    outcomeQuote: outcome && outcome.quote,
    workspaceReceipt: outcome && outcome.workspace_rebase_receipt,
  };
}

const BUSINESS_CHANGE_OBJECT_KEYS = Object.freeze({
  "claim:product.launch_date": "dataJourney.changes.launchDate",
  "policy:finance.currency": "dataJourney.changes.currency",
  "work:finance-approval": "business.impact.financeApproval",
  "work:launch-readiness-review": "business.impact.launchReadiness",
  "work:partner-brief": "business.impact.partnerBrief",
  "work:quote-blue-harbor": "business.quote.blueHarbor",
});

function businessChangeObjectLabel(objectId) {
  const quote = currentState?.quote;
  if (quote && objectId === quote.id && typeof quote.label === "string" && quote.label.trim()) {
    return displayToken(quote.label);
  }
  const key = BUSINESS_CHANGE_OBJECT_KEYS[String(objectId || "")];
  return key ? t(key) : displayToken(objectId || "NOT_OBSERVED");
}

function businessChangeObjectGroup(objectClass, labelKey, objects, applied = false) {
  const items = objects.length
    ? objects.map((item) => {
      const copy = applied
        ? t("businessChange.object.change", {
          object: businessChangeObjectLabel(item.object_id),
          from: displayToken(item.from || "NOT_OBSERVED"),
          to: displayToken(item.to || "NOT_OBSERVED"),
        })
        : t("businessChange.object.state", {
          object: businessChangeObjectLabel(item.object_id),
          state: displayToken(item.state || item.classification || item.reason || "NOT_OBSERVED"),
        });
      return `<li title="${escapeHtml(item.object_id || "")}">${escapeHtml(copy)}</li>`;
    }).join("")
    : `<li>${escapeHtml(t("businessChange.object.none"))}</li>`;
  return `<article data-object-class="${escapeHtml(objectClass)}"><strong>${escapeHtml(t(labelKey))}</strong><ul>${items}</ul></article>`;
}

function quoteVersionFromRef(reference) {
  const match = reference && String(reference).match(/@([^@]+)$/);
  return match ? match[1] : null;
}

function quoteRevisionLabel(version) {
  const match = /^v([1-9]\d*)$/.exec(String(version || ""));
  if (!match) return displayToken("NOT_OBSERVED");
  return t("quote.revision", { number: match[1] });
}

function quoteRevisionPathLabel(versions) {
  return versions.length
    ? versions.map((version) => quoteRevisionLabel(version)).join(" → ")
    : displayToken("NOT_OBSERVED");
}

function quoteDecisionKey(stage) {
  return { PREVIEWED: "event.previewed", APPROVED: "event.approved", RECOVERY_REQUIRED: "event.recoveryRequired" }[stage] || "quote.decision.none";
}

function quoteWorkflowKey() {
  return "event.current";
}

function quoteVersionPath(state, projections = []) {
  const versions = [];
  const firstOutcomeQuote = projections.map((projection) => projection.outcomeQuote).find(Boolean);
  const baseVersion = firstOutcomeQuote && quoteVersionFromRef(firstOutcomeQuote.payload && firstOutcomeQuote.payload.rebased_from);
  if (baseVersion) versions.push(baseVersion);
  projections.forEach((projection) => {
    const version = projection.outcomeQuote && projection.outcomeQuote.version;
    if (version && !versions.includes(version)) versions.push(version);
  });
  const currentVersion = state.quote && state.quote.version;
  if (currentVersion && !versions.includes(currentVersion)) versions.push(currentVersion);
  return versions;
}

function businessChangeRound(projection, round) {
  const labelKey = projection.kind === "launch_date" ? "businessChange.launchDate" : "businessChange.currency";
  const delta = projection.delta;
  const changeValue = delta
    ? `${localizedFactHtml(observed(delta.base_value))}<b aria-hidden="true">→</b>${localizedFactHtml(observed(delta.proposed_value))}`
    : localizedFactHtml("NOT_OBSERVED");
  const agents = projection.agents.length
    ? projection.agents.map((agent) => escapeHtml(displayToken(agent))).join(" + ")
    : localizedFactHtml("NOT_OBSERVED");
  const owner = projection.owner ? escapeHtml(displayToken(projection.owner)) : localizedFactHtml("NOT_OBSERVED");
  const reviewState = projection.reviewWaitSatisfied && projection.approvalDigest
    ? t("businessChange.reviewPassed")
    : t("businessChange.reviewPending");
  const impactObserved = projection.receiptComplete || projection.previewImpactValid;
  const metric = (key) => impactObserved
    ? localizedFactHtml(projection.metrics[key])
    : localizedFactHtml("NOT_OBSERVED");
  const objectLabels = projection.receiptComplete
    ? ["businessChange.object.applied", "businessChange.object.preserved", "businessChange.object.review"]
    : [
      "businessChange.object.candidateRebuild",
      "businessChange.object.candidatePreserve",
      "businessChange.object.candidateReview",
    ];
  return `<article class="business-change-round" data-kind="${escapeHtml(projection.kind)}" data-phase="${escapeHtml(projection.impactPhase)}">
    <header><span>${escapeHtml(t("businessChange.round", { round }))}</span><strong>${escapeHtml(t(labelKey))}</strong><div>${changeValue}</div></header>
    <div class="business-change-people">
      <div><span>${escapeHtml(t("businessChange.agents"))}</span><strong>${agents}</strong></div>
      <div><span>${escapeHtml(t("businessChange.owner"))}</span><strong>${owner}</strong><small>${escapeHtml(reviewState)}</small></div>
    </div>
    <dl>
      <div><dt>${escapeHtml(t("businessChange.rebuilt"))}</dt><dd>${metric("rebuilt")}</dd></div>
      <div><dt>${escapeHtml(t("businessChange.preserved"))}</dt><dd>${metric("preserved")}</dd></div>
      <div><dt>${escapeHtml(t("businessChange.unknown"))}</dt><dd>${metric("unknown")}</dd></div>
      ${projection.receiptComplete ? `<div><dt>${escapeHtml(t("businessChange.falseInvalidations"))}</dt><dd>${metric("falseInvalidations")}</dd></div>` : ""}
      ${projection.receiptComplete ? `<div><dt>${escapeHtml(t("businessChange.unauthorized"))}</dt><dd>${metric("unauthorized")}</dd></div>` : ""}
    </dl>
    ${impactObserved ? `<div class="business-change-objects">
      ${businessChangeObjectGroup("applied", objectLabels[0], projection.appliedObjects, projection.receiptComplete)}
      ${businessChangeObjectGroup("preserved", objectLabels[1], projection.preservedObjects)}
      ${businessChangeObjectGroup("review", objectLabels[2], projection.reviewObjects)}
    </div>` : ""}
    ${projection.receiptComplete
      ? ""
      : `<p>${escapeHtml(t(projection.previewImpactValid ? "businessChange.previewObserved" : "businessChange.notObserved"))}</p>`}
  </article>`;
}

function currentChangeAgentRow(agent) {
  const candidate = agent.candidateKind && agent.candidateDigest
    ? t("currentChange.candidateValue", {
      kind: displayToken(agent.candidateKind),
      digest: shortDigest(agent.candidateDigest, 20),
    })
    : displayToken("NOT_OBSERVED");
  const externalTools = agent.tools.filter((tool) => !String(tool).includes("none-proposal-only"));
  const tools = externalTools.length
    ? externalTools.map((tool) => displayToken(tool)).join(" + ")
    : t("currentChange.noTool");
  const skills = agent.skills.length
    ? agent.skills.map((skill) => displayToken(skill)).join(" + ")
    : t("currentChange.noSkill");
  const capability = t("currentChange.capabilityValue", { tools, skills });
  const handoff = agent.handoffTo && agent.handoffDigest
    ? t("currentChange.handoffValue", {
      from: displayToken(agent.handoffFrom),
      to: displayToken(agent.handoffTo),
      digest: shortDigest(agent.handoffDigest, 20),
    })
    : t("currentChange.noHandoff");
  const review = agent.reviewDecision
    ? t("currentChange.reviewValue", {
      decision: displayToken(agent.reviewDecision),
      verifier: displayToken(agent.verifierEvidenceClass || "NOT_OBSERVED"),
    })
    : t("currentChange.noReview");
  const work = t("currentChange.workValue", {
    purpose: displayToken(agent.taskPurpose || "NOT_OBSERVED"),
    scope: agent.contextScope.length ? agent.contextScope.map(displayToken).join(" + ") : displayToken("NOT_OBSERVED"),
    object: displayToken(agent.changeObjectId || "NOT_OBSERVED"),
  });
  return `<li data-agent-actor="${escapeHtml(agent.actor || "NOT_OBSERVED")}" data-candidate-only="${agent.candidateOnly === true ? "true" : "false"}" data-target-writes="${agent.targetWrites === 0 ? "0" : "NOT_OBSERVED"}" data-task-purpose="${escapeHtml(agent.taskPurpose || "NOT_OBSERVED")}" data-context-scope="${escapeHtml(agent.contextScope.join("|"))}" data-change-object-id="${escapeHtml(agent.changeObjectId || "NOT_OBSERVED")}" data-tool-refs="${escapeHtml(agent.tools.join("|"))}" data-skill-refs="${escapeHtml(agent.skills.join("|"))}" data-capability-semantics="version-ref" data-external-tool-observed="${externalTools.length ? "true" : "false"}" data-invocation-status="not-observed" data-handoff-observed="${agent.handoffTo && agent.handoffDigest ? "true" : "false"}" data-review-decision="${escapeHtml(agent.reviewDecision || "NOT_OBSERVED")}" data-reviewer-task-created="false">
    <div data-evidence-field="agent"><span>${escapeHtml(t("currentChange.agent"))}</span><strong>${escapeHtml(displayToken(agent.actor || "NOT_OBSERVED"))}</strong><small>${escapeHtml(displayToken(agent.status || agent.evidenceClass || "NOT_OBSERVED"))}</small></div>
    <div data-evidence-field="candidate"><span>${escapeHtml(t("currentChange.candidate"))}</span><strong title="${escapeHtml(agent.candidateDigest || "")}">${escapeHtml(candidate)}</strong><small data-evidence-field="agent-work">${escapeHtml(work)}</small></div>
    <div data-evidence-field="tool-skill-refs"><span>${escapeHtml(t("currentChange.capability"))}</span><strong>${escapeHtml(capability)}</strong></div>
    <div data-evidence-field="handoff"><span>${escapeHtml(t("currentChange.handoff"))}</span><strong title="${escapeHtml(agent.handoffDigest || "")}">${escapeHtml(handoff)}</strong></div>
    <div data-evidence-field="reviewer-boundary"><span>${escapeHtml(t("currentChange.review"))}</span><strong>${escapeHtml(review)}</strong></div>
  </li>`;
}

function currentChangeEvidenceRound(projection, round) {
  const labelKey = projection.kind === "launch_date" ? "businessChange.launchDate" : "businessChange.currency";
  const delta = projection.delta || {};
  const team = projection.agents.length
    ? projection.agents.map((agent) => displayToken(agent)).join(" + ")
    : displayToken("NOT_OBSERVED");
  const owner = projection.owner ? displayToken(projection.owner) : displayToken("NOT_OBSERVED");
  const writes = projection.candidateTargetWrites === 0 ? "0" : displayToken("NOT_OBSERVED");
  return `<article class="current-change-evidence-round" data-kind="${escapeHtml(projection.kind)}" data-change-team="${escapeHtml(projection.agents.join("|"))}" data-exact-owner="${escapeHtml(projection.owner || "NOT_OBSERVED")}" data-candidate-writes="${escapeHtml(writes)}" data-workflow-run-id="${escapeHtml(projection.workflowRunId || "NOT_OBSERVED")}">
    <header>
      <div><span>${escapeHtml(t("currentChange.round", { round, change: t(labelKey) }))}</span><strong>${localizedFactHtml(observed(delta.base_value))}<b aria-hidden="true">→</b>${localizedFactHtml(observed(delta.proposed_value))}</strong></div>
      <dl>
        <div><dt>${escapeHtml(t("currentChange.team"))}</dt><dd data-evidence-field="change-team">${escapeHtml(team)}</dd></div>
        <div><dt>${escapeHtml(t("currentChange.owner"))}</dt><dd data-evidence-field="exact-owner">${escapeHtml(owner)}</dd></div>
        <div><dt>${escapeHtml(t("currentChange.writes"))}</dt><dd data-evidence-field="canonical-writes">${escapeHtml(writes)}</dd></div>
      </dl>
    </header>
    <ol>${projection.agentEvidence.map(currentChangeAgentRow).join("")}</ol>
  </article>`;
}

function renderCurrentChangeEvidence(state) {
  const panel = byId("current-change-evidence");
  if (!panel) return;
  if (state.schema_version === "orgrebase.workspace-state.v2") { panel.hidden = true; return; }
  const changes = state && state.changes || {};
  const runId = currentRunId(state);
  const projections = [
    changeProjection("launch_date", changes.launch_date, runId),
    changeProjection("currency", changes.currency, runId),
  ].filter((projection) => projection.hasPreview);
  panel.hidden = projections.length === 0;
  if (panel.hidden) {
    panel.removeAttribute("data-round-count");
    panel.removeAttribute("data-candidate-writes");
    panel.removeAttribute("data-evidence-status");
    byId("current-change-evidence-rounds").innerHTML = "";
    text("current-change-evidence-summary", t("currentChange.waiting"));
    return;
  }
  if (projections.some((projection) => !projection.changeEvidenceValid)) {
    panel.dataset.evidenceStatus = "INVALID";
    panel.removeAttribute("data-round-count");
    panel.removeAttribute("data-candidate-writes");
    text("current-change-evidence-summary", t("currentChange.invalid"));
    byId("current-change-evidence-rounds").innerHTML = `<p class="current-change-evidence-invalid">${escapeHtml(t("currentChange.invalid"))}</p>`;
    return;
  }
  const allZeroWrites = projections.every((projection) => projection.candidateTargetWrites === 0);
  panel.dataset.evidenceStatus = "VERIFIED";
  panel.dataset.roundCount = String(projections.length);
  panel.dataset.candidateWrites = allZeroWrites ? "0" : "NOT_OBSERVED";
  text("current-change-evidence-summary", t("currentChange.summary", {
    rounds: projections.length,
    writes: allZeroWrites ? 0 : displayToken("NOT_OBSERVED"),
  }));
  byId("current-change-evidence-rounds").innerHTML = projections
    .map((projection, index) => currentChangeEvidenceRound(projection, index + 1))
    .join("");
}

function summedProjectionMetric(projections, key) {
  const values = projections.map((projection) => projection.metrics[key]);
  return values.length === 2 && values.every((value) => Number.isFinite(Number(value)))
    ? values.reduce((total, value) => total + Number(value), 0)
    : "NOT_OBSERVED";
}

function authorityStep(labelKey, observedState, detail) {
  return `<article class="authority-step ${observedState ? "observed" : "waiting"}">
    <i aria-hidden="true"></i>
    <span>${escapeHtml(t(labelKey))}</span>
    <strong>${escapeHtml(observedState ? t("authorityLadder.observed") : t("authorityLadder.waiting"))}</strong>
    <small>${escapeHtml(observedState ? detail : t("authorityLadder.waiting"))}</small>
  </article>`;
}

function renderAuthorityLadder(state) {
  const root = byId("authority-ladder");
  const competition = activeCompetition(state);
  const runId = currentRunId(state);
  const changes = state.changes || {};
  const events = Array.isArray(state.change_events) ? state.change_events : [];
  const selection = window.OrgRebaseChangeWorkbench?.selectedDetail?.();
  const selectedEvent = selection && events.find((event) => event.event_id === selection.event?.event_id);
  const selected = selection?.execution_run_id === runId && selection.event?.event_id
    && /^sha256:[0-9a-f]{64}$/.test(selection.event.digest || "")
    && (!selectedEvent || selectedEvent.event_digest === selection.event.digest) ? selection : null;
  const progress = selected && window.OrgRebaseChangeWorkbench?.authorityProgress?.();
  if (progress && progress.execution_run_id === runId && progress.event_id === selected.event.event_id
    && progress.event_digest === selected.event.digest && Array.isArray(progress.steps) && progress.steps.length === 6) {
    root.hidden = false;
    text("authority-ladder-title", t("authorityLadder.changeTitle"));
    text("authority-ladder-context", `${progress.event_id} · ${displayToken(selected.status)}`);
    const phaseLabels = Object.fromEntries(["complete", "current", "waiting", "blocked", "unobserved"]
      .map(phase => [phase, t(`authorityLadder.phase.${phase}`)]));
    byId("authority-ladder-steps").innerHTML = progress.steps.map(step => {
      const phase = Object.hasOwn(phaseLabels, step.status) ? step.status : "unobserved";
      return `<article class="authority-step ${phase === "complete" ? "observed" : "waiting"}" data-phase="${phase}" data-step="${escapeHtml(step.key)}">
        <i aria-hidden="true"></i><span>${escapeHtml(step.label)}</span><strong>${phaseLabels[phase]}</strong><small>${escapeHtml(step.text)}</small></article>`;
    }).join("");
    return;
  }
  if (byId("authority-ladder-title")) text("authority-ladder-title", t("authorityLadder.title"));
  const eventId = selected?.event.event_id || state.active_event_id || [...events].reverse().find((event) => changes[event.event_id])?.event_id
    || Object.keys(changes).reverse().find((id) => changes[id]) || null;
  const change = selected || eventId && changes[eventId];
  const event = events.find((item) => item.event_id === eventId);
  const rows = state.execution_activity && Array.isArray(state.execution_activity.rows)
    ? state.execution_activity.rows.filter((row) => sameRunActivityRow(row, runId)) : [];
  if (root) root.hidden = !runId || (!competition && !change);
  text("authority-ladder-context", eventId ? t(selected ? "authorityLadder.selectedContext" : "authorityLadder.context", {
    event: eventId,
    status: displayToken(selected?.status || event?.status || "NOT_OBSERVED"),
  }) : t("authorityLadder.noChange"));

  const atObserved = Boolean(competition && competition.project_terminal_state === "completed"
    && Number.isSafeInteger(competition.agentteams_action_count) && competition.agentteams_action_count > 0);
  const collaboration = competition && competition.agent_collaboration;
  const reviewer = collaboration && collaboration.reviewer;
  const reviewerAuthority = collaboration && collaboration.authority;
  const reviewerAttempts = Object.entries(reviewer || {}).filter(([key, value]) => /^attempt_[1-9][0-9]*$/.test(key)
    && value && typeof value === "object").sort(([left], [right]) => Number(left.slice(8)) - Number(right.slice(8)));
  const finalReview = reviewerAttempts.at(-1);
  const reviewerObserved = Boolean(competition && reviewer && reviewer.target_writes === 0
    && reviewerAuthority && reviewerAuthority.reviewer_target_writes === 0
    && finalReview && finalReview[1].verdict === "PASS"
    && Array.isArray(finalReview[1].missing_domains) && finalReview[1].missing_domains.length === 0
    && Array.isArray(finalReview[1].reason_codes) && finalReview[1].reason_codes.length > 0
    && finalReview[1].reason_codes.every((code) => typeof code === "string" && code.length > 0)
    && competition.summary_digest && rows.some((row) => row.plane === "REVIEWER"
      && row.status === "PASS" && row.target_writes === 0 && row.receipt_digest === competition.summary_digest));

  const preview = change && change.preview;
  const bundle = preview && preview.bundle;
  const previewDigest = preview && preview.preview_digest;
  const changeSetDigest = bundle && bundle.change_set && bundle.change_set.digest;
  const candidateObserved = Boolean(preview && preview.kind === eventId && previewDigest && changeSetDigest
    && bundle.run_envelope && bundle.run_envelope.run_id === runId
    && typeof bundle.run_envelope.nonce === "string" && bundle.run_envelope.nonce.length > 0
    && bundle.preview && bundle.preview.digest === previewDigest
    && rows.some((row) => row.plane === "CONTROL" && row.status === "LOCKED"
      && row.receipt_digest === previewDigest && row.source_ref === preview.artifact_id));
  const approvalEnvelope = change && change.approval;
  const approval = approvalEnvelope && approvalEnvelope.approval;
  const approvalDigest = approvalEnvelope && approvalEnvelope.approval_digest;
  const binding = approvalEnvelope && approvalEnvelope.binding;
  const reviewEvidence = approvalEnvelope && approvalEnvelope.approval_review_evidence;
  const humanObserved = Boolean(candidateObserved && approvalEnvelope && approvalEnvelope.kind === eventId
    && approval && approvalDigest && approval.digest === approvalDigest
    && approval.preview_digest === previewDigest && approval.change_set_digest === changeSetDigest
    && binding && binding.workflow_run_id === runId && binding.change_kind === eventId
    && binding.run_nonce === bundle.run_envelope.nonce
    && binding.approval_digest === approvalDigest && binding.preview_digest === previewDigest
    && binding.change_set_digest === changeSetDigest
    && reviewEvidence && reviewEvidence.review_wait_satisfied === true
    && rows.some((row) => row.plane === "HUMAN" && row.status === "APPROVED"
      && row.receipt_digest === approvalDigest && row.source_ref === approvalEnvelope.artifact_id));
  const outcomeEnvelope = change && change.outcome;
  const outcome = outcomeEnvelope && outcomeEnvelope.outcome;
  const outcomeQuote = outcome && outcome.quote;
  const workspaceReceipt = outcome && outcome.workspace_rebase_receipt;
  const baseReceipt = outcome && outcome.rebase_receipt;
  const quoteRef = outcomeQuote?.id && outcomeQuote?.version ? `${outcomeQuote.id}@${outcomeQuote.version}` : null;
  const canonicalObserved = Boolean(humanObserved && outcomeEnvelope && outcomeEnvelope.kind === eventId
    && outcome && outcome.preview_digest === previewDigest && outcome.approval_digest === approvalDigest
    && outcome.change_set && outcome.change_set.digest === changeSetDigest
    && baseReceipt && baseReceipt.workflow_run_id === runId
    && baseReceipt.run_nonce === binding.run_nonce && baseReceipt.status === "COMPLETED"
    && baseReceipt.approval_digest === approvalDigest
    && baseReceipt.change_set_ref === `${bundle.change_set.id}@${bundle.change_set.revision}`
    && baseReceipt.preview_ref === bundle.preview.id
    && workspaceReceipt && workspaceReceipt.status === "COMPLETED"
    && workspaceReceipt.base_rebase_receipt_digest === baseReceipt.digest
    && quoteRef && Array.isArray(workspaceReceipt.successor_object_refs)
    && workspaceReceipt.successor_object_refs.includes(quoteRef)
    && rows.some((row) => row.plane === "CONTROL" && row.permission === "CANONICAL_WRITE"
      && row.status === "COMPLETED" && row.source_ref === quoteRef
      && row.receipt_digest === workspaceReceipt.digest));
  byId("authority-ladder-steps").innerHTML = [
    authorityStep("authorityLadder.at", atObserved, t("authorityLadder.atDetail", {
      actions: competition && competition.agentteams_action_count,
      terminal: displayToken(competition && competition.project_terminal_state),
    })),
    authorityStep("authorityLadder.reviewer", reviewerObserved, t("authorityLadder.reviewerDetail", {
      attempt: finalReview && finalReview[0].slice(8), verdict: displayToken(finalReview && finalReview[1].verdict),
      writes: reviewer && reviewer.target_writes,
    })),
    authorityStep("authorityLadder.candidate", candidateObserved, t("authorityLadder.candidateDetail")),
    authorityStep("authorityLadder.human", humanObserved, t("authorityLadder.humanDetail", {
      owner: displayToken(approval && approval.actor_id),
    })),
    authorityStep("authorityLadder.canonical", canonicalObserved, t("authorityLadder.canonicalDetail", {
      quote: quoteRevisionLabel(outcomeQuote && outcomeQuote.version), current: quoteRevisionLabel(state.quote && state.quote.version),
    })),
  ].join("");
}

const DATA_JOURNEY_SLOT_KEYS = Object.freeze({
  product_plan: "dataJourney.slot.productPlan",
  launch_date: "dataJourney.slot.launchDate",
  data_residency: "dataJourney.slot.dataResidency",
  notice_required: "dataJourney.slot.noticeRequired",
  price_band: "dataJourney.slot.priceBand",
  pricing_policy: "dataJourney.slot.pricingPolicy",
  quote_basket: "dataJourney.slot.quoteBasket",
  currency: "dataJourney.slot.currency",
  partner_terms: "dataJourney.slot.partnerTerms",
  quote_compose_skill: "dataJourney.slot.quoteComposeSkill",
  public_message: "dataJourney.slot.publicMessage",
});

const DATA_JOURNEY_COMPONENT_KEYS = Object.freeze({
  DOMAIN: "dataJourney.component.domain",
  KNOWLEDGE: "dataJourney.component.knowledge",
  AUTHORITY: "dataJourney.component.authority",
  CAPABILITY: "dataJourney.component.capability",
  DEPENDENCY: "dataJourney.component.dependency",
});

const DATA_JOURNEY_SOURCE_HINTS = Object.freeze([
  ["product-catalog", "dataJourney.source.productCatalog"],
  ["release-plan", "dataJourney.source.releasePlan"],
  ["platform-capability", "dataJourney.source.platformCapability"],
  ["contract-register", "dataJourney.source.contractRegister"],
  ["pricing-policy", "dataJourney.source.pricingPolicy"],
  ["currency-policy", "dataJourney.source.currencyPolicy"],
  ["partner-policy", "dataJourney.source.partnerPolicy"],
  ["skill-registry", "dataJourney.source.skillRegistry"],
  ["public-message", "dataJourney.source.publicMessage"],
]);

const DATA_JOURNEY_AUTHORITY_HINTS = Object.freeze([
  ["skill-registry", "dataJourney.authority.skillRegistry"],
  ["product", "dataJourney.authority.product"],
  ["legal", "dataJourney.authority.legal"],
  ["finance", "dataJourney.authority.finance"],
  ["gtm", "dataJourney.authority.gtm"],
]);

function dataJourneySlotLabel(slotId) {
  const key = DATA_JOURNEY_SLOT_KEYS[String(slotId || "")];
  return key ? t(key) : displayToken(slotId || "NOT_OBSERVED");
}

function dataJourneyComponentLabel(kind) {
  const normalized = String(kind || "").toUpperCase();
  const key = DATA_JOURNEY_COMPONENT_KEYS[normalized];
  return key ? t(key) : displayToken(normalized || "NOT_OBSERVED");
}

function dataJourneySensitivityLabel(value) {
  const key = `dataJourney.sensitivity.${String(value || "").toLowerCase()}`;
  return I18N[currentLanguage][key] || I18N[DEFAULT_LANGUAGE][key] || displayToken(value || "NOT_OBSERVED");
}

function dataJourneyVerdict(value) {
  const normalized = String(value || "").toUpperCase();
  if (normalized === "ADMITTED" || normalized === "TRUSTED_COMPLETE" || normalized === "PASS") {
    return t("dataJourney.verdict.admitted");
  }
  if (normalized === "MATCH" || normalized === "MATCHED") return t("dataJourney.verdict.match");
  if (normalized === "READY") return t("dataJourney.verdict.ready");
  if (normalized === "APPROVED" || normalized === "COMPLETED") return displayToken(normalized);
  if (normalized === "CONSUMED_BY_QUOTE_FORMATION") return t("dataJourney.verdict.consumed");
  if (normalized === "NOT_USED_IN_THIS_RUN") return t("dataJourney.verdict.notUsed");
  const localized = displayToken(normalized);
  if (localized !== normalized) return localized;
  return t("dataJourney.verdict.notObserved");
}

function dataJourneyReferenceLabel(value, hints) {
  if (!value) return displayToken("NOT_OBSERVED");
  const raw = String(value);
  const normalized = raw.toLowerCase();
  const match = hints.find(([needle]) => normalized.includes(needle));
  if (match) return t(match[1]);
  const exactTranslation = displayToken(raw);
  if (exactTranslation !== raw) return exactTranslation;
  return t("dataJourney.source.registeredReference");
}

function structuredBusinessValue(value, slotId, language) {
  if (value === null || typeof value !== "object") return null;
  const displayLanguage = language === "en" ? "en" : DEFAULT_LANGUAGE;
  const percent = points => Number.isSafeInteger(points) && points >= 0 && points <= 10000
    ? `${Math.floor(points / 100)}.${String(points % 100).padStart(2, "0")}%` : "—";
  if (slotId === "pricing_policy") {
    return t("businessValue.pricingPolicy", {
      discount: percent(value.discount_bps), tax: percent(value.tax_bps),
      label: typeof value.tax_label === "string" ? value.tax_label : "—",
    }, displayLanguage);
  }
  if (slotId === "quote_basket" && Array.isArray(value.items)) {
    return t("businessValue.quoteBasket", {
      count: value.items.length, currency: typeof value.currency === "string" ? value.currency : "—",
    }, displayLanguage);
  }
  const serialized = JSON.stringify(value);
  return serialized.length > 400 ? `${serialized.slice(0, 400)}…` : serialized;
}

function dataJourneyObserved(value, slotId) {
  if (value === null || value === undefined || value === "") return displayToken("NOT_OBSERVED");
  if (typeof value === "boolean") return displayToken(value ? "TRUE" : "FALSE");
  const structured = structuredBusinessValue(value, slotId, currentLanguage);
  if (structured !== null) return structured;
  return displayToken(value);
}

function dataJourneyQuoteReference(value) {
  if (!value) return displayToken("NOT_OBSERVED");
  const version = quoteVersionFromRef(value) || String(value);
  return quoteRevisionLabel(version);
}

function setEnterpriseDataJourneyStep(id, stepState, summary, bodyHtml) {
  const step = byId(id);
  step.dataset.state = stepState;
  const summaryNode = step.querySelector(":scope > small");
  summaryNode.textContent = summary;
  let body = step.querySelector(":scope > .enterprise-data-journey-step-body");
  if (!body) {
    body = document.createElement("div");
    body.className = "enterprise-data-journey-step-body";
    step.appendChild(body);
  }
  body.innerHTML = bodyHtml;
}

function renderEnterpriseDataSource(source) {
  const values = source && Array.isArray(source.values) ? source.values : [];
  const sourceReady = Boolean(
    source
    && source.status === "ADMITTED_AND_MATCHED"
    && values.length > 0
  );
  const componentCount = source && Number.isFinite(Number(source.component_count))
    ? Number(source.component_count)
    : 0;
  const summary = sourceReady
    ? t("dataJourney.source.summary", { count: values.length, components: componentCount })
    : t("dataJourney.source.waiting");
  const body = sourceReady
    ? `<ul class="enterprise-data-source-values">${values.map((value) => {
      const sourceLabel = dataJourneyReferenceLabel(value.source_ref, DATA_JOURNEY_SOURCE_HINTS);
      const authorityLabel = dataJourneyReferenceLabel(value.authority_ref, DATA_JOURNEY_AUTHORITY_HINTS);
      const capabilityRequirement = String(value.semantic_kind || "").toUpperCase() === "SKILL"
        ? `<small class="capability-requirement-ref">${escapeHtml(t("dataJourney.source.capabilityRequirement"))}</small>`
        : "";
      return `<li data-domain="${escapeHtml(value.domain_id || "NOT_OBSERVED")}" title="${escapeHtml(value.object_ref || "")}">
        <div><span>${escapeHtml(dataJourneySlotLabel(value.slot_id))}</span><em>${escapeHtml(dataJourneySensitivityLabel(value.sensitivity))}</em></div>
        <strong>${escapeHtml(dataJourneyObserved(value.value, value.slot_id))}</strong>
        <small>${escapeHtml(t("dataJourney.source.provenance", { source: sourceLabel, authority: authorityLabel }))}</small>
        ${capabilityRequirement}
      </li>`;
    }).join("")}</ul>`
    : `<p class="enterprise-data-journey-unobserved">${escapeHtml(t("dataJourney.source.waiting"))}</p>`;
  setEnterpriseDataJourneyStep("enterprise-data-journey-source", sourceReady ? "complete" : "waiting", summary, body);
}

function renderEnterpriseDataOac(oac) {
  const bindings = oac && Array.isArray(oac.component_bindings) ? oac.component_bindings : [];
  const sourceAdmissionVerdict = String(oac && oac.source_admission_verdict || "").toUpperCase();
  const runtimeProjectionVerdict = String(oac && oac.runtime_projection_verdict || "").toUpperCase();
  const admission = dataJourneyVerdict(sourceAdmissionVerdict);
  const projection = dataJourneyVerdict(runtimeProjectionVerdict);
  const activationStatus = oac && oac.activation_status;
  const activationConsumed = String(activationStatus || "").toUpperCase() === "CONSUMED_BY_QUOTE_FORMATION";
  const executionBound = String(oac && oac.execution_binding_status || "").toUpperCase()
    === "OAC_BOUND_EXECUTION_PLAN_REALIZED"
    && oac && oac.topology_match === true;
  const selectedDomains = oac && Array.isArray(oac.selected_domain_ids)
    ? oac.selected_domain_ids
    : [];
  const exactBindingKinds = new Set(bindings.map((binding) => String(binding && binding.kind || "").toUpperCase()));
  const bindingsExact = bindings.length === 5
    && exactBindingKinds.size === 5
    && Object.keys(DATA_JOURNEY_COMPONENT_KEYS).every((kind) => exactBindingKinds.has(kind))
    && bindings.every((binding) => (
      String(binding && binding.source_admission_verdict || "").toUpperCase() === "ADMITTED"
      && String(binding && binding.runtime_projection_status || "").toUpperCase() === "MATCH"
    ));
  const oacReady = sourceAdmissionVerdict === "ADMITTED"
    && runtimeProjectionVerdict === "MATCH"
    && bindingsExact
    && activationConsumed
    && executionBound;
  const summary = bindings.length
    ? oacReady
      ? t("dataJourney.oac.summaryBound", {
        count: bindings.length,
        domains: selectedDomains.length,
      })
      : activationConsumed
        ? t("dataJourney.oac.summaryActivated", { count: bindings.length })
        : t("dataJourney.oac.summaryPending", { count: bindings.length })
    : t("dataJourney.oac.waiting");
  const body = bindings.length ? `<div class="enterprise-data-oac-bindings">
    <p class="enterprise-data-journey-control-state">${escapeHtml(t("dataJourney.oac.activation", { status: dataJourneyVerdict(activationStatus) }))}</p>
    <p class="enterprise-data-journey-explainer">${escapeHtml(t(executionBound ? "dataJourney.oac.boundary" : "dataJourney.oac.boundaryPending"))}</p>
  </div>`
    : `<p class="enterprise-data-journey-unobserved">${escapeHtml(t("dataJourney.oac.waiting"))}</p>`;
  const stepState = oacReady ? "complete" : bindings.length ? "active" : "waiting";
  setEnterpriseDataJourneyStep("enterprise-data-journey-oac", stepState, summary, body);
}

function renderEnterpriseDataContext(context) {
  const actors = context && Array.isArray(context.actors) ? context.actors : [];
  const ready = context && context.status === "READY" && actors.length > 0;
  const summary = ready
    ? t("dataJourney.context.summary", {
      domains: context.domain_actor_count,
      actors: context.actor_count,
      slots: context.slot_count,
    })
    : t("dataJourney.context.waiting");
  const body = ready ? `<div>
    <ul class="enterprise-data-context-actors">${actors.map((actor) => {
      const slots = Array.isArray(actor.included_slot_ids) && actor.included_slot_ids.length
        ? actor.included_slot_ids.map(dataJourneySlotLabel).join(" · ")
        : t("dataJourney.context.noSlots");
      const reasons = contextExclusionSummary(actor);
      return `<li title="${escapeHtml(actor.projection_digest || "")}">
        <div><strong>${escapeHtml(displayToken(actor.actor_id || "NOT_OBSERVED"))}</strong><em>${escapeHtml(displayToken(actor.actor_kind || "NOT_OBSERVED"))}</em></div>
        <span>${escapeHtml(slots)}</span>
        <small>${escapeHtml(t("dataJourney.context.actor", {
          included: actor.included_count,
          excluded: actor.excluded_count,
        }))}</small>
        ${reasons ? `<small>${escapeHtml(reasons)}</small>` : ""}
      </li>`;
    }).join("")}</ul>
    <p class="enterprise-data-journey-explainer">${escapeHtml(t("dataJourney.context.authority"))}</p>
  </div>`
    : `<p class="enterprise-data-journey-unobserved">${escapeHtml(t("dataJourney.context.waiting"))}</p>`;
  setEnterpriseDataJourneyStep("enterprise-data-journey-context", ready ? "complete" : "waiting", summary, body);
}

function renderEnterpriseDataQuotes(quotes) {
  const versions = quotes && Array.isArray(quotes.versions) ? quotes.versions : [];
  const currentVersion = quotes && quotes.current_version;
  const ready = quotes && quotes.status === "READY" && versions.length > 0;
  const summary = versions.length
    ? t("dataJourney.quotes.summary", { count: versions.length, version: quoteRevisionLabel(currentVersion) })
    : t("dataJourney.quotes.waiting");
  const versionItems = versions.map((quote) => {
    const payload = quote && quote.payload || {};
    return `<li data-quote-version="${escapeHtml(quote && quote.version || "NOT_OBSERVED")}">
      <strong>${escapeHtml(t("dataJourney.quotes.version", {
        version: quoteRevisionLabel(quote && quote.version),
        launch: dataJourneyObserved(payload.launch_date),
        currency: dataJourneyObserved(payload.currency),
      }))}</strong>
    </li>`;
  }).join("");
  const incomplete = versions.length && !ready
    ? `<p class="enterprise-data-journey-unobserved">${escapeHtml(t("dataJourney.quotes.incomplete"))}</p>`
    : "";
  const body = versions.length
    ? `<ol class="enterprise-data-quote-versions">${versionItems}</ol>${incomplete}`
    : `<p class="enterprise-data-journey-unobserved">${escapeHtml(t("dataJourney.quotes.waiting"))}</p>`;
  setEnterpriseDataJourneyStep("enterprise-data-journey-quotes", ready ? "complete" : versions.length ? "active" : "waiting", summary, body);
}

function enterpriseDataJourneyMetric(metrics, key) {
  return metrics && Object.prototype.hasOwnProperty.call(metrics, key)
    ? dataJourneyObserved(metrics[key])
    : displayToken("NOT_OBSERVED");
}

function enterpriseDataJourneyChange(change) {
  const kind = change && change.kind;
  const delta = change && change.delta;
  const labelKey = kind === "launch_date" ? "dataJourney.changes.launchDate" : "dataJourney.changes.currency";
  const complete = Boolean(change
    && change.status === "COMPLETED"
    && delta
    && change.approval_status === "APPROVED"
    && change.receipt_status === "COMPLETED"
    && change.workspace_receipt_status === "COMPLETED");
  const owner = change && (change.approval_actor_id || change.owner_id);
  const metrics = change && change.metrics;
  const previewMetrics = change && change.candidate_metrics;
  const previewObserved = Boolean(
    !complete
      && change
      && change.status === "PREVIEWED"
      && change.preview_evidence_status === "VMRC_EXACT_BOUND"
      && previewMetrics,
  );
  const deltaCopy = delta
    ? t("dataJourney.changes.delta", {
      from: dataJourneyObserved(delta.base_value),
      to: dataJourneyObserved(delta.proposed_value),
    })
    : displayToken("NOT_OBSERVED");
  const ownerCopy = previewObserved
    ? t("dataJourney.changes.previewOwner", { owner: displayToken(owner || "NOT_OBSERVED") })
    : t("dataJourney.changes.owner", {
      owner: displayToken(owner || "NOT_OBSERVED"),
      approval: dataJourneyVerdict(change && change.approval_status),
    });
  const transitionCopy = previewObserved
    ? t("dataJourney.changes.previewTransition", {
      before: dataJourneyQuoteReference(change && change.predecessor_ref),
    })
    : t("dataJourney.changes.transition", {
      before: dataJourneyQuoteReference(change && change.predecessor_ref),
      after: dataJourneyQuoteReference(change && change.successor_ref),
      receipt: dataJourneyVerdict(change && change.workspace_receipt_status),
    });
  const metricsCopy = previewObserved
    ? t("dataJourney.changes.previewMetrics", {
      rebuilt: enterpriseDataJourneyMetric(previewMetrics, "affected_hard"),
      preserved: enterpriseDataJourneyMetric(previewMetrics, "bounded_unaffected"),
      unknown: enterpriseDataJourneyMetric(previewMetrics, "human_review"),
    })
    : t("dataJourney.changes.metrics", {
      rebuilt: enterpriseDataJourneyMetric(metrics, "work_items_rebased"),
      preserved: enterpriseDataJourneyMetric(metrics, "bounded_unaffected"),
      unknown: enterpriseDataJourneyMetric(metrics, "unknown"),
      falseInvalidations: enterpriseDataJourneyMetric(metrics, "false_invalidations"),
      unauthorized: enterpriseDataJourneyMetric(metrics, "unauthorized_disclosures"),
    });
  return `<article data-change-kind="${escapeHtml(kind || "NOT_OBSERVED")}" data-state="${complete ? "complete" : previewObserved ? "preview" : "waiting"}">
    <header><span>${escapeHtml(t(labelKey))}</span><strong>${escapeHtml(deltaCopy)}</strong></header>
    <p>${escapeHtml(ownerCopy)}</p>
    <p>${escapeHtml(transitionCopy)}</p>
    <small>${escapeHtml(metricsCopy)}</small>
    ${complete ? "" : `<em>${escapeHtml(t(previewObserved ? "dataJourney.changes.previewObserved" : "dataJourney.changes.notObserved"))}</em>`}
  </article>`;
}

function renderEnterpriseDataJourney(state) {
  const lineage = state && state.enterprise_data_lineage;
  const section = byId("enterprise-data-journey");
  const lineageStatus = lineage && lineage.status || "WAITING_FOR_FORMATION";
  const targetWrites = lineage && lineage.read_model_target_writes;
  const displayedWrites = targetWrites === null || targetWrites === undefined
    ? displayToken("NOT_OBSERVED")
    : targetWrites;
  section.dataset.lineageStatus = lineageStatus;
  section.dataset.readModelTargetWrites = String(targetWrites === null || targetWrites === undefined ? "NOT_OBSERVED" : targetWrites);
  section.dataset.state = lineageStatus === "READY" ? "ready" : lineageStatus === "IN_PROGRESS" ? "progress" : "waiting";

  const source = lineage && lineage.source || {};
  const context = lineage && lineage.context || {};
  const quotes = lineage && lineage.quotes || {};
  const values = Array.isArray(source.values) ? source.values : [];
  text("enterprise-data-journey-status", lineageStatus === "READY"
    ? t("dataJourney.status.ready", {
      facts: values.length,
      slots: context.slot_count,
      version: quoteRevisionLabel(quotes.current_version),
    })
    : lineageStatus === "IN_PROGRESS"
      ? t("dataJourney.status.progress", { writes: displayedWrites })
      : t("dataJourney.status.waiting", { writes: displayedWrites }));

  renderEnterpriseDataSource(source);
  renderEnterpriseDataOac(lineage && lineage.oac || {});
  renderEnterpriseDataContext(context);
  renderEnterpriseDataQuotes(quotes);

  const changes = lineage && Array.isArray(lineage.changes)
    ? lineage.changes.filter((change) => change && (change.delta || change.status !== "NOT_OBSERVED"))
    : [];
  const changesPanel = byId("enterprise-data-journey-changes");
  changesPanel.hidden = state.schema_version === "orgrebase.workspace-state.v2";
  if (changesPanel.hidden) { changesPanel.innerHTML = ""; return; }
  changesPanel.innerHTML = changes.length
    ? `<header><strong>${escapeHtml(t("dataJourney.changes.title"))}</strong></header><div>${changes.map(enterpriseDataJourneyChange).join("")}</div>`
    : `<p>${escapeHtml(t("dataJourney.changes.waiting"))}</p>`;
}

function renderBusinessChangeBoard(state) {
  renderAuthorityLadder(state);
  if (state.schema_version === "orgrebase.workspace-state.v2") { byId("business-change-board").hidden = true; return; }
  const changes = state.changes || {};
  const runId = currentRunId(state);
  const projections = [
    changeProjection("launch_date", changes.launch_date, runId),
    changeProjection("currency", changes.currency, runId),
  ].filter((projection) => projection.delta);
  const board = byId("business-change-board");
  board.hidden = projections.length === 0;
  if (board.hidden) {
    byId("business-change-rounds").innerHTML = "";
    byId("business-change-total").innerHTML = "";
    return;
  }
  const versions = quoteVersionPath(state, projections);
  text("business-change-version", t("businessChange.versionPath", {
    path: quoteRevisionPathLabel(versions),
    stage: displayToken(state.stage),
  }));
  byId("business-change-rounds").innerHTML = projections
    .map((projection, index) => businessChangeRound(projection, index + 1))
    .join("");
  const complete = projections.filter((projection) => projection.receiptComplete).length;
  if (complete === 2) {
    byId("business-change-total").innerHTML = `<div><span>${escapeHtml(t("businessChange.totalTitle"))}</span><strong>${escapeHtml(t("businessChange.totalSummary", {
      rebuilt: summedProjectionMetric(projections, "rebuilt"),
      preserved: summedProjectionMetric(projections, "preserved"),
      unknown: summedProjectionMetric(projections, "unknown"),
    }))}</strong><small>${escapeHtml(t("businessChange.totalSecurity", {
      falseInvalidations: summedProjectionMetric(projections, "falseInvalidations"),
      unauthorized: summedProjectionMetric(projections, "unauthorized"),
    }))}</small></div>`;
  } else {
    const previewed = projections.filter((projection) => projection.previewImpactValid).length;
    byId("business-change-total").innerHTML = `<div><span>${escapeHtml(t("businessChange.totalTitle"))}</span><strong>${escapeHtml(t("businessChange.progressSummary", { previewed, completed: complete }))}</strong></div>`;
  }
  const next = byId("business-change-next");
  if (next) {
    const completeRun = state.business_complete === true;
    next.hidden = false;
    next.dataset.routeTarget = completeRun ? "validation" : "quote";
    next.textContent = t(completeRun ? "businessChange.archiveAction" : "businessChange.reviewAction");
  }
}

function selectiveRound(kind, label, change) {
  const preview = change && change.preview;
  const bundle = preview && (preview.bundle || preview);
  const changeSet = bundle && bundle.change_set;
  const delta = changeSet && Array.isArray(changeSet.deltas) ? changeSet.deltas[0] : null;
  const approval = change && change.approval;
  const approvalDigest = approval && (approval.approval_digest || approval.artifact_digest || (approval.approval && approval.approval.digest));
  const receipt = receiptForChange(change);
  const metrics = (receipt && receipt.metrics) || {};
  const rebuilt = countOrObserved(receipt && (receipt.rebuilt_object_refs || receipt.rebuilt), metrics.work_items_rebased);
  const preserved = countOrObserved(receipt && (receipt.preserved_object_refs || receipt.preserved), metrics.bounded_unaffected);
  const unknown = countOrObserved(receipt && (receipt.unknown_object_refs || receipt.unknown), metrics.unknown);
  const falseInvalidations = metrics.false_invalidations ?? (receipt && receipt.false_invalidations) ?? "NOT_OBSERVED";
  const unauthorized = metrics.unauthorized_disclosures ?? (receipt && receipt.unauthorized_disclosures) ?? "NOT_OBSERVED";
  return `<article class="selective-round">
    <div class="selective-round-head"><span>${escapeHtml(kind)}</span><strong>${escapeHtml(label)}</strong><small>${delta ? `${localizedFactHtml(observed(delta.base_value))} → ${localizedFactHtml(observed(delta.proposed_value))}` : localizedFactHtml("NOT_OBSERVED")}</small></div>
    <dl>
      <div><dt>${escapeHtml(t("selective.rebuilt"))}</dt><dd>${localizedFactHtml(rebuilt)}</dd></div>
      <div><dt>${escapeHtml(t("selective.preserved"))}</dt><dd>${localizedFactHtml(preserved)}</dd></div>
      <div><dt>${escapeHtml(t("selective.unknown"))}</dt><dd>${localizedFactHtml(unknown)}</dd></div>
      <div><dt>${escapeHtml(t("selective.falseInvalidations"))}</dt><dd>${localizedFactHtml(falseInvalidations)}</dd></div>
      <div><dt>${escapeHtml(t("selective.unauthorized"))}</dt><dd>${localizedFactHtml(unauthorized)}</dd></div>
    </dl>
    <details class="selective-round-audit">
      <summary><span>${escapeHtml(t("selective.auditDetails"))}</span><b aria-hidden="true">＋</b></summary>
      <div><span>${escapeHtml(t("selective.approvalDigest"))}</span><code title="${escapeHtml(observed(approvalDigest))}">${approvalDigest ? escapeHtml(shortDigest(approvalDigest, 34)) : localizedFactHtml("NOT_OBSERVED")}</code></div>
    </details>
  </article>`;
}

function renderSelectiveChangeCard(state) {
  const card = byId("selective-change-card");
  // The object-level data-evolution board is the sole v1→v2→v3 terminal view.
  // Keep this compatibility mount hidden so older integrations do not break.
  card.hidden = true;
  card.setAttribute("aria-hidden", "true");
  byId("selective-change-rounds").innerHTML = "";
}

const PROCESS_LABEL_KEYS = {
  "quote-step:intake": "process.intake",
  "quote-step:product-confirmation": "process.product",
  "quote-step:legal-confirmation": "process.legal",
  "quote-step:finance-confirmation": "process.finance",
  "quote-step:composition": "process.compose",
  "quote-step:delivery-acceptance": "process.accept",
  "quote-step:change-impact": "process.impact",
  "quote-step:selective-update": "process.update",
};

// The declared baseline is the source of truth. This fallback keeps older
// read-model responses understandable while rolling upgrades add the explicit
// to_be_responsible field; it never changes execution or approval authority.
const TO_BE_RESPONSIBLE_BY_PROCESS_STEP = Object.freeze({
  "quote-step:intake": Object.freeze(["Quote Operator"]),
  "quote-step:product-confirmation": Object.freeze(["Product Agent"]),
  "quote-step:legal-confirmation": Object.freeze(["Legal Agent"]),
  "quote-step:finance-confirmation": Object.freeze(["Finance Agent"]),
  "quote-step:composition": Object.freeze(["GTM Agent"]),
  "quote-step:delivery-acceptance": Object.freeze(["Quote Operator"]),
  "quote-step:change-impact": Object.freeze(["Deterministic Control"]),
  "quote-step:selective-update": Object.freeze(["Bounded Runtime", "Canonical Writer"]),
});

const VALUE_METRIC_LABEL_KEYS = {
  quote_cycle_elapsed_minutes: "metric.cycle",
  policy_confirmation_active_minutes: "metric.policy",
  impact_exact_mismatch_case_rate: "metric.mismatch",
  first_pass_rework_rate: "metric.rework",
  unauthorized_access_success_rate: "metric.access",
  hold_for_review_decision_rate: "metric.review",
  selective_rebase_cost_saving_rate: "metric.saving",
};

function percent(value, digits = 1) {
  return Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(digits)}%` : "—";
}

function metricValue(metric) {
  if (metric.maturity === "NOT_RUN") return displayToken("NOT_RUN");
  if (metric.unit === "RATIO") return percent(metric.value);
  return metric.value ?? "—";
}

function metricDetail(metric) {
  if (metric.metric_id === "impact_exact_mismatch_case_rate") return t("metric.detail.mismatch", metric);
  if (metric.metric_id === "unauthorized_access_success_rate") return t("metric.detail.security", metric);
  if (metric.metric_id === "hold_for_review_decision_rate") return t("metric.detail.review", metric);
  if (metric.metric_id === "selective_rebase_cost_saving_rate") return t("metric.detail.cost", {
    full: Number(metric.full_rebuild_normalized_cost).toFixed(2),
    selective: Number(metric.selective_rebase_normalized_cost).toFixed(2),
  });
  return t("metric.detail.noShadow");
}

function formattedInteger(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return new Intl.NumberFormat(currentLanguage === "en" ? "en-US" : "zh-CN", {
    maximumFractionDigits: 0,
  }).format(numeric);
}

function setProofOverviewState(id, state, labelKey, ...auditValues) {
  const node = byId(id);
  if (!node) return;
  node.dataset.state = state;
  node.textContent = t(labelKey);
  exactAuditTitle(id, ...auditValues);
}

function releaseFactsProofProjection(release) {
  const nonEmptyString = (value) => typeof value === "string" && value.trim().length > 0;
  const emptyFailures = (value) => Boolean(value && Array.isArray(value.failures) && value.failures.length === 0);
  const optionalFailuresEmpty = (value) => Boolean(
    value && (!Object.prototype.hasOwnProperty.call(value, "failures")
      || (Array.isArray(value.failures) && value.failures.length === 0)),
  );
  const observedCount = (value) => Number.isSafeInteger(value) && value >= 0;
  const completedCountPair = (total, completed, { requireObserved = false } = {}) => (
    observedCount(total)
      && observedCount(completed)
      && completed === total
      && (!requireObserved || total > 0)
  );
  const sameStringSet = (left, right) => (
    Array.isArray(left)
      && Array.isArray(right)
      && left.length > 0
      && left.length === right.length
      && left.every(nonEmptyString)
      && right.every(nonEmptyString)
      && new Set(left).size === left.length
      && new Set(right).size === right.length
      && [...left].sort().every((value, index) => value === [...right].sort()[index])
  );
  const releaseSchemaValid = Boolean(
    release
      && release.schema_version === "orgrebase.release-facts.v9"
      && release.current_semifinal_delivery
      && release.product_path_blackbox,
  );
  const delivery = releaseSchemaValid ? release.current_semifinal_delivery : {};
  const golden = delivery.golden_quote_delivery || {};
  const goldenRunId = nonEmptyString(golden.run_id) ? golden.run_id : null;
  const quote = golden.quote || {};
  const sameRun = golden.same_run_agentteams_skill_tool || {};
  const oacFormation = golden.oac_bound_task_formation || {};
  const ownerGateEnvelope = golden.human_owner_gates || {};
  const ownerGates = Array.isArray(ownerGateEnvelope.gates) ? ownerGateEnvelope.gates : [];
  const ownerGateKinds = ownerGates.map((gate) => gate && gate.change_kind);
  const ownerGatesBound = Boolean(
    ownerGateEnvelope.status === "PASS"
      && observedCount(ownerGateEnvelope.count)
      && ownerGateEnvelope.count > 0
      && ownerGateEnvelope.count === ownerGates.length
      && new Set(ownerGateKinds).size === ownerGateKinds.length
      && ownerGates.every((gate) => (
        gate
          && gate.run_id === goldenRunId
          && nonEmptyString(gate.owner_id)
          && nonEmptyString(gate.change_kind)
          && gate.review_wait_satisfied === true
          && isSha256Digest(gate.approval_digest)
          && isSha256Digest(gate.binding_digest)
      )),
  );
  const sameRunBound = Boolean(
    sameRun.status === "PASS"
      && sameRun.run_id === goldenRunId
      && isSha256Digest(sameRun.agentteams_summary_digest)
      && isSha256Digest(sameRun.tool_receipt_digest)
      && isSha256Digest(sameRun.skill_invocation_receipt_digest),
  );
  const oacFormationBound = Boolean(
    oacFormation.status === "PASS"
      && emptyFailures(oacFormation)
      && oacFormation.run_id === goldenRunId
      && oacFormation.task_intake_status === "FORMATION_COMPLETED"
      && oacFormation.activation_status === "CONSUMED_BY_QUOTE_FORMATION"
      && isSha256Digest(oacFormation.activation_binding_digest)
      && isSha256Digest(oacFormation.task_formation_decision_receipt_digest)
      && isSha256Digest(oacFormation.context_envelope_digest)
      && isSha256Digest(oacFormation.agentteams_execution_plan_digest)
      && sameStringSet(
        oacFormation.planned_domain_ids,
        oacFormation.actual_agentteams_domain_ids,
      )
      && oacFormation.topology_match === true
      && oacFormation.candidate_only === true
      && oacFormation.canonical_target_writes === 0,
  );
  const goldenPassed = Boolean(
    releaseSchemaValid
      && delivery.status === "PASS"
      && emptyFailures(delivery)
      && golden.status === "PASS"
      && emptyFailures(golden)
      && goldenRunId
      && quote.status === "PASS"
      && quote.stage === "QUOTE_V3"
      && quote.version === "v3"
      && isSha256Digest(quote.digest)
      && sameRunBound
      && oacFormationBound
      && ownerGatesBound
      && golden.agents_are_candidate_only === true
      && golden.candidate_layer_canonical_target_writes === 0
      && golden.production_ready === false,
  );

  const formation = delivery.dynamic_formation_validation || {};
  const selectedDomains = Array.isArray(formation.selected_domain_ids)
    ? formation.selected_domain_ids
    : [];
  const formationRunId = nonEmptyString(formation.run_id) ? formation.run_id : null;
  const formationCountsBound = Boolean(
    selectedDomains.length >= 2
      && selectedDomains.every(nonEmptyString)
      && new Set(selectedDomains).size === selectedDomains.length
      && observedCount(formation.domain_task_count)
      && formation.domain_task_count === selectedDomains.length
      && observedCount(formation.reviewer_task_count)
      && formation.reviewer_task_count > 0
      && observedCount(formation.task_binding_count)
      && formation.task_binding_count
        === formation.domain_task_count + formation.reviewer_task_count
      && observedCount(formation.agentteams_action_count)
      && formation.agentteams_action_count >= formation.task_binding_count,
  );
  const formationPassed = Boolean(
    releaseSchemaValid
      && delivery.status === "PASS"
      && emptyFailures(delivery)
      && delivery.claim_boundary
      && delivery.claim_boundary.golden_and_dynamic_formation_are_independent_runs === true
      && formation.status === "PASS"
      && emptyFailures(formation)
      && formationRunId
      && goldenRunId
      && formationRunId !== goldenRunId
      && formation.evidence_paths
      && nonEmptyString(formation.evidence_paths.verification)
      && nonEmptyString(formation.evidence_paths.receipt)
      && formationCountsBound
      && formation.topology_match === true
      && formation.candidate_only === true
      && formation.canonical_target_writes === 0
      && formation.production_ready === false,
  );

  const productPath = releaseSchemaValid ? release.product_path_blackbox : {};
  const sourceProjection = productPath.source_runtime_projection || {};
  const productPathEvidenceValid = Boolean(
    releaseSchemaValid
      && productPath.status === "PASS"
      && optionalFailuresEmpty(productPath)
      && productPath.benchmark_version === "ProductPath-v0.3-task-intake-bound"
      && productPath.evidence_class === "LOCAL_REAL_HTTP_BLACKBOX"
      && nonEmptyString(productPath.evidence_path)
      && completedCountPair(productPath.case_count, productPath.cases_passed, { requireObserved: true })
      && completedCountPair(productPath.mutation_count, productPath.mutations_killed, { requireObserved: true })
      && completedCountPair(productPath.integrity_attacks, productPath.integrity_attacks_rejected, { requireObserved: true })
      && completedCountPair(productPath.gate_attacks, productPath.gate_attacks_rejected, { requireObserved: true })
      && observedCount(productPath.uvicorn_processes_started)
      && productPath.uvicorn_processes_started > 0
      && observedCount(productPath.real_process_restarts)
      && productPath.real_process_restarts > 0
      && productPath.single_byte_tamper === "PASS"
      && productPath.retained_evaluator_replay === "PASS"
      && productPath.retained_raw_observations === "PASS"
      && productPath.source_bound_oracle === "PASS"
      && productPath.benchmark_migration === "PASS"
      && productPath.task_intake_same_run_id === true
      && productPath.task_intake_same_workspace_nonce === true
      && productPath.task_intake_natural_language_authority === false
      && productPath.source_canonical_target_writes === 0
      && completedCountPair(sourceProjection.total, sourceProjection.matched, { requireObserved: true })
      && productPath.external_iam === "NOT_RUN"
      && productPath.real_enterprise_generalization === "NOT_CLAIMED",
  );
  const productPathPassed = productPathEvidenceValid
    && productPath.reconstructed_wheel_bytes === "PASS" && productPath.current_release_qualified === true;
  const productPathHistorical = productPathEvidenceValid
    && productPath.reconstructed_wheel_bytes === "NOT_RUN"
    && productPath.fact_role === "HISTORICAL_COMPATIBILITY_BASELINE"
    && productPath.current_release_qualified === false;
  return {
    releaseSchemaValid,
    delivery,
    golden,
    goldenPassed,
    formation,
    formationPassed,
    productPath,
    productPathPassed,
    productPathHistorical,
  };
}

function proofFailureCodes(...records) {
  return records.flatMap((record) => (
    record && Array.isArray(record.failures) ? record.failures : []
  ));
}

function publicValidationProofProjection(validation) {
  const nonEmptyString = (value) => typeof value === "string" && value.trim().length > 0;
  const optionalFailuresEmpty = (value) => Boolean(
    value && (!Object.prototype.hasOwnProperty.call(value, "failures")
      || (Array.isArray(value.failures) && value.failures.length === 0)),
  );
  const verification = validation && validation.verification || {};
  const source = validation && validation.source || {};
  const passed = Boolean(
    validation
      && validation.schema_version === "orgrebase.public-real-process-validation-view.v1"
      && validation.status === "PASS"
      && optionalFailuresEmpty(validation)
      && validation.generated === true
      && validation.read_model_target_writes === 0
      && validation.archive_status === "FROZEN_HISTORICAL_VALIDATION"
      && validation.current_task_run === false
      && nonEmptyString(validation.run_id)
      && verification.status === "PASS"
      && isSha256Digest(verification.benchmark_receipt_digest)
      && isSha256Digest(verification.replay_receipt_digest)
      && isSha256Digest(verification.projection_digest)
      && isSha256Digest(verification.summary_digest)
      && isSha256Digest(source.raw_sha256)
      && nonEmptyString(validation.claim_boundary),
  );
  const agentic = passed ? validation.agentic_adaptation || {} : {};
  const agenticVerification = agentic.verification || {};
  const agenticPassed = Boolean(
    passed
      && agentic.status === "PASS"
      && optionalFailuresEmpty(agentic)
      && nonEmptyString(agentic.adaptation_run_id)
      && nonEmptyString(agentic.execution_run_id)
      && agentic.adaptation_run_id !== validation.run_id
      && agentic.execution_run_id !== validation.run_id
      && agentic.adaptation_run_id !== agentic.execution_run_id
      && agentic.model && agentic.model.status === "VALID"
      && agentic.model.provider_request_observed === true
      && agentic.agentteams && agentic.agentteams.terminal_state === "completed"
      && Number.isSafeInteger(agentic.agentteams.action_count)
      && agentic.agentteams.action_count > 0
      && agentic.mapping && agentic.mapping.deterministic_verdict === "PASS"
      && Number.isSafeInteger(agentic.mapping.unknown_count)
      && agentic.mapping.unknown_count >= 0
      && agentic.execution && agentic.execution.canonical_target_writes === 0
      && agentic.read_model_target_writes === 0
      && isSha256Digest(agenticVerification.closed_world_manifest_digest)
      && isSha256Digest(agenticVerification.adaptation_receipt_digest)
      && Array.isArray(agentic.limitations)
      && agentic.limitations.includes("NOT_ARBITRARY_ENTERPRISE_ADAPTATION"),
  );
  return { passed, agentic, agenticPassed, source, verification };
}

function renderCurrentProofOverview() {
  const release = currentReleaseFacts;
  const projection = releaseFactsProofProjection(release);
  const {
    delivery,
    golden,
    goldenPassed,
    formation,
    formationPassed,
    productPath,
    productPathPassed,
    productPathHistorical,
  } = projection;
  const goldenFailures = proofFailureCodes(delivery, golden, golden.oac_bound_task_formation);
  setProofOverviewState(
    "proof-chain-golden",
    release === null ? "loading" : goldenPassed ? "pass" : "unavailable",
    release === null ? "proofOverview.loading" : goldenPassed ? "proofOverview.pass" : "proofOverview.unavailable",
    ...(goldenPassed
      ? [golden.run_id, golden.quote && golden.quote.digest]
      : goldenFailures),
  );

  setProofOverviewState(
    "proof-chain-formation",
    release === null ? "loading" : formationPassed ? "pass" : "unavailable",
    release === null ? "proofOverview.loading" : formationPassed ? "proofOverview.pass" : "proofOverview.unavailable",
    ...(formationPassed
      ? [formation.run_id, formation.evidence_paths && formation.evidence_paths.verification]
      : proofFailureCodes(delivery, formation)),
  );

  setProofOverviewState(
    "proof-product-path",
    release === null ? "loading" : productPathPassed ? "pass" : productPathHistorical ? "historical" : "unavailable",
    release === null ? "proofOverview.loading" : productPathPassed ? "proofOverview.pass" : productPathHistorical ? "proofOverview.historical" : "proofOverview.unavailable",
    ...(productPathPassed || productPathHistorical
      ? [productPath.benchmark_version, productPath.evidence_path]
      : proofFailureCodes(productPath)),
  );
  text(
    "product-path-detail",
    release === null
      ? t("proofOverview.product.loading")
      : productPathPassed || productPathHistorical
        ? t("proofOverview.product.detail", {
          casesPassed: formattedInteger(productPath.cases_passed),
          caseCount: formattedInteger(productPath.case_count),
          mutationsKilled: formattedInteger(productPath.mutations_killed),
          mutationCount: formattedInteger(productPath.mutation_count),
          integrityRejected: formattedInteger(productPath.integrity_attacks_rejected),
          integrityTotal: formattedInteger(productPath.integrity_attacks),
          gateRejected: formattedInteger(productPath.gate_attacks_rejected),
          gateTotal: formattedInteger(productPath.gate_attacks),
          processes: formattedInteger(productPath.uvicorn_processes_started),
          restarts: formattedInteger(productPath.real_process_restarts),
        }) + (productPathHistorical ? ` ${t("proofOverview.historicalBuild")}` : "")
        : t("proofOverview.unavailable"),
  );

  const oac = currentOacAdaptationProof;
  const oacGate = oac && (oac.workspaceGate || oac.workspace_gate) || null;
  const oacPassed = Boolean(
    oacGate
      && oacGate.valid === true
      && (oacGate.formAllowed === true || (oacGate.status === "CONSUMED_BY_QUOTE_FORMATION"
        && isSha256Digest(oacGate.consumptionReceiptDigest)))
      && ["READY_TO_FORM", "CONSUMED_BY_QUOTE_FORMATION"].includes(oacGate.status)
      && isSha256Digest(oacGate.activationBindingDigest),
  );
  setProofOverviewState(
    "proof-chain-oac",
    oac === null ? "loading" : oacPassed ? "pass" : "waiting",
    oac === null ? "proofOverview.loading" : oacPassed ? "proofOverview.pass" : "proofOverview.waitingOac",
  );

  const publicValidation = currentPublicRealProcessValidation;
  const publicProjection = publicValidationProofProjection(publicValidation);
  const bpiPassed = publicProjection.passed;
  const agentic = publicProjection.agentic;
  const agenticPassed = publicProjection.agenticPassed;
  setProofOverviewState(
    "proof-chain-bpi",
    publicValidation === null ? "loading" : bpiPassed ? "pass" : "unavailable",
    publicValidation === null ? "proofOverview.loading" : bpiPassed ? "proofOverview.pass" : "proofOverview.unavailable",
    ...(bpiPassed
      ? [publicValidation.run_id, publicProjection.verification.projection_digest]
      : proofFailureCodes(publicValidation)),
  );
  setProofOverviewState(
    "proof-chain-agentic",
    publicValidation === null ? "loading" : agenticPassed ? "pass" : "unavailable",
    publicValidation === null ? "proofOverview.loading" : agenticPassed ? "proofOverview.pass" : "proofOverview.unavailable",
    ...(agenticPassed
      ? [agentic.adaptation_run_id, agentic.execution_run_id, agentic.verification && agentic.verification.adaptation_receipt_digest]
      : proofFailureCodes(agentic)),
  );
}

function renderPublicRealProcessValidation(validation) {
  currentPublicRealProcessValidation = validation;
  const proof = publicValidationProofProjection(validation);
  const passed = proof.passed;
  const unavailable = !validation || validation.status === "UNAVAILABLE";
  const status = byId("public-validation-status");
  status.dataset.state = passed ? "pass" : unavailable ? "unavailable" : "fail";
  status.textContent = passed
    ? t("publicValidation.pass")
    : unavailable
      ? t("publicValidation.unavailable")
      : t("publicValidation.fail");
  text(
    "public-validation-description",
    passed
      ? t("publicValidation.descriptionWithSource", {
        source: currentLanguage === "en"
          ? ((validation.dataset || {}).source_nature_en || "BPI Challenge 2019")
          : ((validation.dataset || {}).source_nature_zh || "BPI Challenge 2019"),
      })
      : unavailable
        ? t("publicValidation.unavailableDescription")
        : t("publicValidation.failDescription"),
  );

  const dataset = passed ? (validation.dataset || {}) : {};
  const scenario = passed ? (validation.scenario || {}) : {};
  const scopes = passed ? (validation.action_scopes || {}) : {};
  const assurance = passed ? (validation.assurance || {}) : {};
  const source = passed ? (validation.source || {}) : {};
  const verification = passed ? (validation.verification || {}) : {};
  const adaptation = passed ? proof.agentic : {};
  const adaptationPassed = proof.agenticPassed;
  const adaptationModel = adaptationPassed ? (adaptation.model || {}) : {};
  const adaptationAgentTeams = adaptationPassed ? (adaptation.agentteams || {}) : {};
  const adaptationMapping = adaptationPassed ? (adaptation.mapping || {}) : {};
  const adaptationAdmission = adaptationPassed ? (adaptation.admission || {}) : {};
  const adaptationExecution = adaptationPassed ? (adaptation.execution || {}) : {};
  const vendor = scopes.vendor_broadcast || {};
  const documentScope = scopes.purchase_document || {};
  const oacScope = scopes.oac_typed_item || {};

  const adaptationPanel = byId("public-oac-adaptation");
  adaptationPanel.hidden = !passed;
  text(
    "public-validation-method",
    adaptationPassed ? t("publicValidation.adaptation.method") : t("publicValidation.method"),
  );
  if (passed) {
    const modelName = {
      "gemini-3.7-flash": "Gemini 3.7 Flash",
      "gemini-3.8-flash": "Gemini 3.8 Flash",
    }[adaptationModel.model_id] || adaptationModel.model_id || "—";
    text(
      "public-adaptation-mapper",
      adaptationPassed
        ? t("publicValidation.adaptation.mapperValue", {
          model: modelName,
          provider: adaptationModel.provider || "—",
          evidence: displayToken(adaptationModel.evidence_class),
          count: formattedInteger(adaptationAgentTeams.action_count),
        })
        : "—",
    );
    text(
      "public-adaptation-validator",
      adaptationPassed ? t("publicValidation.adaptation.validatorPass") : "—",
    );
    text(
      "public-adaptation-unknowns",
      adaptationPassed
        ? t("publicValidation.adaptation.unknownValue", {
          count: formattedInteger(adaptationMapping.unknown_count),
          dimensions: Array.isArray(adaptationMapping.unknown_dimensions)
            ? adaptationMapping.unknown_dimensions.map((item) => displayToken(item)).join(" / ")
            : "—",
        })
        : "—",
    );
    text(
      "public-adaptation-gate",
      adaptationPassed
        ? t("publicValidation.adaptation.gateValue", {
          elapsed: Number.isFinite(Number(adaptationAdmission.elapsed_ms))
            ? (Number(adaptationAdmission.elapsed_ms) / 1000).toFixed(3)
            : "—",
        })
        : "—",
    );
    text(
      "public-adaptation-queries",
      adaptationPassed
        ? t("publicValidation.adaptation.queryValue", {
          count: formattedInteger(adaptationExecution.query_count),
        })
        : "—",
    );
    text(
      "public-adaptation-recall",
      adaptationPassed ? percent(adaptationExecution.recall, 0) : "—",
    );
    text(
      "public-adaptation-unsafe",
      adaptationPassed
        ? formattedInteger(adaptationExecution.unsafe_false_unaffected_rate)
        : "—",
    );
    text(
      "public-adaptation-writes",
      adaptationPassed ? formattedInteger(adaptationExecution.canonical_target_writes) : "—",
    );
    text(
      "public-adaptation-boundary",
      adaptationPassed
        ? t("publicValidation.adaptation.boundary")
        : t("publicValidation.adaptation.unavailable"),
    );
  }

  text("public-validation-traces", passed ? formattedInteger(dataset.purchase_order_item_traces) : "—");
  text("public-validation-events", passed ? formattedInteger(dataset.events) : "—");
  text("public-validation-activities", passed ? formattedInteger(dataset.activity_types) : "—");
  text("public-validation-queries", passed ? formattedInteger(scenario.query_count) : "—");
  text("public-scope-vendor", passed ? formattedInteger(vendor.action_scope) : "—");
  text("public-scope-document", passed ? formattedInteger(documentScope.action_scope) : "—");
  text("public-scope-oac", passed ? formattedInteger(oacScope.action_scope) : "—");
  text(
    "public-scope-document-reduction",
    passed ? t("publicValidation.scope.reduction", { value: percent(documentScope.safe_reduction, 4) }) : "—",
  );
  text(
    "public-scope-oac-reduction",
    passed ? t("publicValidation.scope.reduction", { value: percent(oacScope.safe_reduction, 4) }) : "—",
  );
  text("public-validation-recall", passed ? percent(assurance.query_contract_recall, 0) : "—");
  text("public-validation-unsafe", passed ? formattedInteger(assurance.unsafe_false_unaffected) : "—");
  text("public-validation-lineage", passed ? percent(assurance.lineage_closure_rate, 0) : "—");
  text("public-validation-selection", passed
    ? t("publicValidation.selection", {
      eligible: formattedInteger(scenario.eligible_query_count),
      selected: formattedInteger(scenario.query_count),
    })
    : t("publicValidation.selectionWaiting"));
  text(
    "public-validation-boundary",
    adaptationPassed ? t("publicValidation.boundaryAdapted") : t("publicValidation.boundary"),
  );
  text("public-validation-doi", passed ? source.doi : "—");
  text("public-validation-license", passed ? source.license : "—");
  text("public-validation-source-hash", passed ? shortDigest(source.raw_sha256, 34) : "—");
  text("public-validation-receipt", passed ? shortDigest(verification.benchmark_receipt_digest, 34) : "—");
  text("public-validation-verification", passed ? shortDigest(verification.replay_receipt_digest, 34) : "—");
  text("public-validation-run-id", passed ? shortDigest(validation.run_id, 42) : "—");
  text(
    "public-validation-run-id-summary",
    t("publicValidation.runSummary", {
      runId: passed ? shortDigest(validation.run_id, 30) : "—",
    }),
  );
  const archiveIdentityDigest = passed
    ? validation.archive_identity_digest || verification.projection_digest
    : null;
  text("public-validation-archive-time", archiveIdentityDigest ? shortDigest(archiveIdentityDigest, 34) : "—");
  text("public-adaptation-run-id", adaptationPassed ? shortDigest(adaptation.adaptation_run_id, 42) : "—");
  text("public-execution-run-id", adaptationPassed ? shortDigest(adaptation.execution_run_id, 42) : "—");
  text("public-adaptation-run-id-visible", adaptationPassed ? shortDigest(adaptation.adaptation_run_id, 42) : "—");
  text("public-execution-run-id-visible", adaptationPassed ? shortDigest(adaptation.execution_run_id, 42) : "—");
  exactAuditTitle("public-validation-run-id", passed ? validation.run_id : null);
  exactAuditTitle("public-validation-run-id-summary", passed ? validation.run_id : null);
  exactAuditTitle("public-validation-archive-time", archiveIdentityDigest);
  exactAuditTitle("public-adaptation-run-id", adaptationPassed ? adaptation.adaptation_run_id : null);
  exactAuditTitle("public-execution-run-id", adaptationPassed ? adaptation.execution_run_id : null);
  exactAuditTitle("public-adaptation-run-id-visible", adaptationPassed ? adaptation.adaptation_run_id : null);
  exactAuditTitle("public-execution-run-id-visible", adaptationPassed ? adaptation.execution_run_id : null);
  const sourceAnchor = byId("public-validation-source");
  sourceAnchor.textContent = passed ? (dataset.title || source.official_record || "—") : "—";
  if (passed && typeof source.official_record === "string" && source.official_record.startsWith("https://")) {
    sourceAnchor.href = source.official_record;
  } else {
    sourceAnchor.removeAttribute("href");
  }
  renderCurrentProofOverview();
}

function isObservedCount(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function currentRunValueProjection(state) {
  const stage = state && state.stage || "EMPTY";
  if (state?.schema_version != null && !["orgrebase.workspace-state.v1", "orgrebase.workspace-state.v2"].includes(state.schema_version)) return { status: "INVALID", stage };
  if (!state || !state.business_complete) return { status: "WAITING", stage };
  const incompleteHistory = state.change_history && state.change_history.total !== Object.keys(state.changes || {}).length;
  if (incompleteHistory && state.schema_version !== "orgrebase.workspace-state.v2") return { status: "INCOMPLETE_HISTORY", stage };
  const execution = stateExecution(state || {});
  const declaredExecutionRunId = state && state.execution && state.execution.run_id;
  const competition = activeCompetition(state || {});
  const runIdentityBound = Boolean(
    declaredExecutionRunId
      && execution.run_id === declaredExecutionRunId
      && (!competition || competition.run_id === declaredExecutionRunId),
  );
  const quote = state && state.quote || {};
  const current = state.schema_version === "orgrebase.workspace-state.v2";
  const archive = typeof currentRunArchive === "undefined" ? null : currentRunArchive;
  let entries = Object.entries(state.changes || {});
  if (current) {
    if (!currentRunArchiveProof(archive, declaredExecutionRunId)) return { status: "WAITING_FOR_VERIFIED_ARCHIVE", stage };
    const dispositions = archive.record.change_dispositions;
    const visible = state.change_events;
    if (!Array.isArray(dispositions) || !Array.isArray(visible)
      || dispositions.length !== state.change_history?.total
      || new Set(dispositions.map(item => item.event_id)).size !== dispositions.length
      || dispositions.some(item => !["APPLIED", "REJECTED", "GROUP_APPLIED"].includes(item.status)
        || !isSha256Digest(item.event_digest))
      || visible.some(event => !dispositions.some(item => event.event_id === item.event_id && event.slot_id === item.slot_id
          && event.owner_id === item.owner_id && event.status === item.status
          && isSha256Digest(item.event_digest) && item.event_digest === event.event_digest))) return { status: "INVALID", stage };
    if (incompleteHistory) {
      const summary = completedRunObservabilityProjection(
        typeof currentRunObservability === "undefined" ? null : currentRunObservability, archive, declaredExecutionRunId);
      if (!runIdentityBound || archive.record.quote.digest !== quote.digest || archive.record.quote.ref !== `${quote.id}@${quote.version}`
        || !summary?.summary) return { status: "WAITING_FOR_VERIFIED_SUMMARY", stage };
      return { status: "PASS", summary: true, runId: declaredExecutionRunId, quoteVersion: quote.version,
        quoteDigest: quote.digest, receiptCount: archive.record.selective_rebase_receipts.length,
        approvalCount: archive.record.human_approval_count };
    }
    if (dispositions.length !== visible.length) return { status: "INVALID", stage };
    const applied = archive.record.selective_rebase_receipts;
    if (dispositions.length === 0) {
      const intake = state.task_intake;
      const events = state.event_scopes?.quote_business;
      const bound = runIdentityBound && state.change_history?.total === 0 && state.change_history?.pending === 0
        && Object.keys(state.changes || {}).length === 0 && applied.length === 0 && archive.record.human_approval_count === 0
        && quote.version === "v1" && archive.record.quote.ref === `${quote.id}@v1` && archive.record.quote.digest === quote.digest
        && intake?.schema_version === "orgrebase.workspace-task-intake-run-receipt.v1" && intake.status === "FORMATION_COMPLETED"
        && intake.run_id === declaredExecutionRunId && intake.quote_ref === archive.record.quote.ref
        && intake.intake_persisted === true && intake.intake_canonical_target_writes === 0
        && intake.formation_authority === "ORGREBASE_CONTROL_PLANE"
        && [intake.digest, intake.candidate_digest, intake.approval_digest, intake.formation_receipt_digest,
          intake.artifact_payload_digest, intake.event_digest, quote.digest].every(isSha256Digest)
        && state.formation?.digest === intake.formation_receipt_digest
        && events?.status === "PASS" && Number.isSafeInteger(events.events) && events.events > 0
        && events.events === archive.record.quote_event_count;
      return bound ? { status: "BASELINE_VERIFIED", stage, runId: declaredExecutionRunId,
        quoteVersion: quote.version, quoteDigest: quote.digest, receiptCount: 0, approvalCount: 0 }
        : { status: "INVALID", stage };
    }
    for (const receipt of applied) {
      if (receipt.kind === "source_readmission_group") {
        if (archive.schema_version !== "orgrebase.workspace-current-run-archive-view.v3"
          || !window.OrgRebaseSourceReadmissionProof.bound(receipt, declaredExecutionRunId, dispositions)) return { status: "INVALID", stage };
        continue;
      }
      const approval = state.changes[receipt.kind]?.approval;
      const event = visible.find(item => item.event_id === receipt.kind);
      if (receipt.approval_authority !== undefined) {
        if (!isSha256Digest(event?.event_digest) || !archiveApprovalAuthorityBound(receipt, declaredExecutionRunId, event.event_digest)
          || !approval?.authority || canonicalAuthorityDocument(approval.authority) !== canonicalAuthorityDocument(receipt.approval_authority)
          || approval.approval?.actor_id !== receipt.approval_actor_id) return { status: "INVALID", stage };
        const grant = receipt.approval_authority.grant;
        if (grant && (grant.tenant_id !== state.enterprise_seed_profile?.organization_id || grant.workspace_id !== state.workspace_id
          || !Number.isFinite(Date.parse(approval.approval.approved_at))
          || Date.parse(approval.approval.approved_at) < Date.parse(grant.issued_at)
          || Date.parse(approval.approval.approved_at) >= Date.parse(grant.expires_at))) return { status: "INVALID", stage };
      } else if (approval?.authority !== undefined) return { status: "INVALID", stage };
    }
    const singles = applied.filter(receipt => receipt.kind !== "source_readmission_group");
    const groups = applied.filter(receipt => receipt.kind === "source_readmission_group");
    if (singles.length !== dispositions.filter(item => item.status === "APPLIED").length
      || singles.some(receipt => !dispositions.some(item => item.event_id === receipt.kind && item.status === "APPLIED" && item.owner_id === receipt.owner_id))
      || groups.reduce((total, receipt) => total + receipt.event_ids.length, 0) !== dispositions.filter(item => item.status === "GROUP_APPLIED").length) return { status: "INVALID", stage };
    entries = singles.map(receipt => [receipt.kind, state.changes[receipt.kind]]);
    if (entries.some(([id, records]) => !records || records.preview?.preview_digest !== applied.find(receipt => receipt.kind === id).preview_digest
      || records.approval?.approval_digest !== applied.find(receipt => receipt.kind === id).approval_digest
      || receiptForChange(records)?.digest !== applied.find(receipt => receipt.kind === id).rebase_receipt_digest
      || records.outcome?.outcome?.workspace_rebase_receipt?.digest !== applied.find(receipt => receipt.kind === id).workspace_receipt_digest)) return { status: "INVALID", stage };
    if (archive.record.quote.digest !== quote.digest || archive.record.quote.ref !== `${quote.id}@${quote.version}`) return { status: "INVALID", stage };
  }
  const projections = current && archive.schema_version === "orgrebase.workspace-current-run-archive-view.v3"
    ? archive.record.selective_rebase_receipts.map(receipt => {
      if (receipt.kind !== "source_readmission_group") return changeProjection(receipt.kind, state.changes[receipt.kind]);
      const outcome = receipt.group_evidence.outcome, metrics = outcome.rebase_receipt.metrics;
      return { receiptComplete: true, receipt: outcome.rebase_receipt,
        workspaceReceipt: outcome.workspace_rebase_receipt, outcomeQuote: outcome.quote,
        metrics: { rebuilt: metrics.work_items_rebased, preserved: metrics.bounded_unaffected,
          unknown: metrics.unknown, falseInvalidations: metrics.false_invalidations,
          unauthorized: metrics.unauthorized_disclosures } };
    })
    : entries.map(([eventId, records]) => changeProjection(eventId, records));
  const requiredMetricKeys = [
    "work_items_rebased",
    "bounded_unaffected",
    "unknown",
    "skills_requalified",
    "false_invalidations",
    "unauthorized_disclosures",
  ];
  const sameRun = runIdentityBound && projections.every((projection) => (
    projection.receiptComplete
      && projection.receipt
      && projection.receipt.digest
      && projection.receipt.workflow_run_id === execution.run_id
      && requiredMetricKeys.every((key) => isObservedCount(
        projection.receipt.metrics && projection.receipt.metrics[key],
      ))
      && projection.workspaceReceipt
      && projection.workspaceReceipt.status === "COMPLETED"
      && projection.workspaceReceipt.digest
      && projection.outcomeQuote
  ));
  const quoteEventScope = state && state.event_scopes && state.event_scopes.quote_business || {};
  const quoteEventsBound = quoteEventScope.status === "PASS" && Number(quoteEventScope.events) > 0;
  const finalQuote = projections.length && projections[projections.length - 1].outcomeQuote;
  const finalQuoteBound = Boolean(
    quote.digest
      && finalQuote
      && finalQuote.digest === quote.digest
      && finalQuote.version === quote.version,
  );
  if (!sameRun || !finalQuoteBound || !quoteEventsBound) {
    return { status: "INVALID", stage, runId: execution.run_id || null };
  }
  const sum = (key) => projections.reduce((total, projection) => {
    const value = projection.metrics[key];
    return total + value;
  }, 0);
  const rebuilt = sum("rebuilt");
  const preserved = sum("preserved");
  const unknown = sum("unknown");
  const requalified = projections.reduce((total, projection) => {
    const metrics = projection.receipt && projection.receipt.metrics || {};
    return total + metrics.skills_requalified;
  }, 0);
  const falseInvalidations = sum("falseInvalidations");
  const unauthorized = sum("unauthorized");
  return {
    status: "PASS",
    runId: execution.run_id,
    quoteVersion: quote.version,
    quoteDigest: quote.digest,
    receiptCount: projections.length,
    receiptDigests: projections.map((projection) => projection.receipt.digest).filter(Boolean),
    decisions: rebuilt + preserved + unknown + requalified,
    rebuilt,
    preserved,
    unknown,
    requalified,
    falseInvalidations,
    unauthorized,
  };
}

function currentRunValueCard(labelKey, value, detailKey, evidence, detailValues = {}) {
  return `<article class="metric-card observed-local-product">
    <span>${escapeHtml(t(labelKey))}</span>
    <strong>${escapeHtml(String(value))}</strong>
    <small>${escapeHtml(t(detailKey, detailValues))}</small>
    <em>${escapeHtml(evidence)}</em>
  </article>`;
}

function validatedModelledSaving(evidence) {
  if (
    !evidence
    || evidence.status !== "PASS"
    || evidence.verification_status !== "PASS"
    || evidence.current_pack_pilot_run !== false
  ) return null;
  const value = evidence.value_and_responsibility || {};
  const cost = value && value.cost_model || {};
  const metrics = value && Array.isArray(value.metrics) ? value.metrics : [];
  const metric = metrics.find((item) => item.metric_id === "selective_rebase_cost_saving_rate") || {};
  const envelope = cost.declared_scenario_envelope || {};
  const stress = cost.stress_envelope || {};
  const finite = (...values) => values.every((item) => Number.isFinite(item));
  const close = (left, right) => finite(left, right) && Math.abs(left - right) <= 1e-12;
  const numericFactsValid = finite(
    cost.full_rebuild,
    cost.selective_rebase,
    cost.saving_rate,
    metric.value,
    metric.numerator,
    metric.denominator,
    metric.full_rebuild_normalized_cost,
    metric.selective_rebase_normalized_cost,
    envelope.lower,
    envelope.upper,
    stress.lower,
    stress.upper,
  ) && cost.full_rebuild > 0 && metric.denominator > 0;
  const formulaSaving = numericFactsValid
    ? (cost.full_rebuild - cost.selective_rebase) / cost.full_rebuild
    : Number.NaN;
  const valid = value && value.claim_ceiling === "SYNTHETIC_CONTROLLED_VALUE_PROOF"
    && numericFactsValid
    && cost.unit === "NORMALIZED_ACTION_COST"
    && cost.evidence_class === "MODELLED_COUNTERFACTUAL"
    && cost.enterprise_roi === "NOT_RUN"
    && cost.full_rebuild >= cost.selective_rebase
    && cost.selective_rebase >= 0
    && typeof cost.receipt_digest === "string"
    && /^sha256:[0-9a-f]{64}$/.test(cost.receipt_digest)
    && envelope.lower <= cost.saving_rate && cost.saving_rate <= envelope.upper
    && envelope.interpretation === "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL"
    && stress.lower <= envelope.lower && envelope.upper <= stress.upper
    && stress.interpretation === "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL"
    && metric.status === "CALCULATED"
    && metric.maturity === "MODELLED"
    && metric.evidence_class === "MODELLED_COUNTERFACTUAL"
    && metric.unit === "RATIO"
    && close(metric.value, cost.saving_rate)
    && close(metric.full_rebuild_normalized_cost, cost.full_rebuild)
    && close(metric.selective_rebase_normalized_cost, cost.selective_rebase)
    && close(metric.denominator, cost.full_rebuild)
    && close(metric.numerator, cost.full_rebuild - cost.selective_rebase)
    && close(formulaSaving, cost.saving_rate);
  if (!valid) return null;
  return { cost, envelope, stress };
}

function renderCurrentRunValue(state, retainedEvidence = currentRetainedEvidence) {
  const projection = currentRunValueProjection(state || {});
  if (projection.status === "BASELINE_VERIFIED") {
    byId("value-metrics").innerHTML = `<article class="metric-card observed-local-product"><span>${escapeHtml(t("value.current.baseline.title"))}</span><strong>${escapeHtml(quoteRevisionLabel(projection.quoteVersion))}</strong><small>${escapeHtml(t("value.current.baseline.detail"))}</small></article>`;
    byId("value-cost-model").innerHTML = `<span>${escapeHtml(t("value.current.cost.label"))}</span><strong>${escapeHtml(t("value.current.baseline.noChanges"))}</strong><small>${escapeHtml(t("value.current.baseline.detail"))}</small>`;
    return;
  }
  if (projection.status !== "PASS") {
    const invalid = projection.status === "INVALID";
    byId("value-metrics").innerHTML = `<article class="metric-card not-run">
      <span>${escapeHtml(t("value.current.waiting.label"))}</span>
      <strong>${escapeHtml(t(invalid ? "value.current.invalid.value" : "value.current.waiting.value"))}</strong>
      <small>${escapeHtml(t(invalid ? "value.current.invalid.detail" : "value.current.waiting.detail"))}</small>
      <em>${escapeHtml(invalid
        ? t("value.current.invalid.evidence")
        : t("value.current.waiting.evidence", { stage: displayToken(projection.stage) }))}</em>
    </article>`;
    byId("value-cost-model").innerHTML = `
      <span>${escapeHtml(t("value.current.cost.label"))}</span>
      <strong>${escapeHtml(invalid ? t("value.current.invalid.value") : t("value.current.cost.waiting"))}</strong>
      <small>${escapeHtml(invalid ? t("value.current.invalid.evidence") : t("value.current.cost.waitingDetail"))}</small>`;
    return;
  }

  const evidence = t("value.current.metric.evidence");
  if (projection.summary) {
    byId("value-metrics").innerHTML = [
      currentRunValueCard("value.current.metric.quote", quoteRevisionLabel(projection.quoteVersion), "value.current.metric.quote.detail", evidence),
      currentRunValueCard("value.current.metric.receipts", projection.receiptCount, "value.current.metric.receipts.detail", evidence),
      currentRunValueCard("value.current.metric.approvals", projection.approvalCount, "value.current.metric.approvals.detail", evidence),
    ].join("");
    byId("value-cost-model").innerHTML = `<span>${escapeHtml(t("value.current.cost.label"))}</span><strong>${escapeHtml(t("value.current.summary.label"))}</strong><small>${escapeHtml(t("value.current.summary.detail"))}</small>`;
    return;
  }
  byId("value-metrics").innerHTML = [
    currentRunValueCard("value.current.metric.quote", quoteRevisionLabel(projection.quoteVersion), "value.current.metric.quote.detail", evidence),
    currentRunValueCard("value.current.metric.receipts", String(projection.receiptCount), "value.current.metric.receipts.detail", evidence),
    currentRunValueCard("value.current.metric.decisions", projection.decisions, "value.current.metric.decisions.detail", evidence),
    currentRunValueCard("value.current.metric.rebuilt", projection.rebuilt, "value.current.metric.rebuilt.detail", evidence),
    currentRunValueCard("value.current.metric.preserved", projection.preserved, "value.current.metric.preserved.detail", evidence),
    currentRunValueCard("value.current.metric.held", projection.unknown, "value.current.metric.held.detail", evidence),
    currentRunValueCard(
      "value.current.metric.safety",
      projection.falseInvalidations + projection.unauthorized,
      "value.current.metric.safety.detail",
      evidence,
      projection,
    ),
  ].join("");
  const modelled = state.schema_version === "orgrebase.workspace-state.v2" ? null : validatedModelledSaving(retainedEvidence);
  byId("value-cost-model").innerHTML = modelled ? `
    <span>${escapeHtml(t("value.current.cost.modelledLabel"))}</span>
    <strong>${escapeHtml(t("value.current.cost.modelledSummary", {
      full: modelled.cost.full_rebuild.toFixed(2),
      selective: modelled.cost.selective_rebase.toFixed(2),
      saving: percent(modelled.cost.saving_rate),
    }))}</strong>
    <small>${t("value.current.cost.modelledDetail", {
      lower: percent(modelled.envelope.lower),
      upper: percent(modelled.envelope.upper),
      stressLower: percent(modelled.stress.lower),
      stressUpper: percent(modelled.stress.upper),
    })}</small>` : `
    <span>${escapeHtml(t("value.current.cost.label"))}</span>
    <strong>${escapeHtml(t("value.current.cost.summary", projection))}</strong>
    <small>${t("value.current.cost.detail", projection)}</small>`;
  exactAuditTitle("value-cost-model");
}

function acceptanceStoryStep(index, titleKey, detailKey, observedState) {
  return `<article class="acceptance-story-step ${observedState ? "observed" : "pending"}">
    <span>${String(index).padStart(2, "0")}</span>
    <div><strong>${escapeHtml(t(titleKey))}</strong><small>${escapeHtml(t(detailKey))}</small></div>
    <em>${escapeHtml(t(observedState ? "acceptance.story.observed" : "acceptance.story.pending"))}</em>
  </article>`;
}

function acceptanceChangeCard(projection, titleKey) {
  if (!projection || !projection.receiptComplete || !projection.delta || !projection.approvalDigest || !projection.reviewWaitSatisfied) {
    return `<article class="acceptance-change-card pending">
      <strong>${escapeHtml(t(titleKey))}</strong>
      <small>${escapeHtml(t("acceptance.story.change.waiting"))}</small>
    </article>`;
  }
  return `<article class="acceptance-change-card observed">
    <span>${escapeHtml(t(titleKey))}</span>
    <strong>${escapeHtml(t("acceptance.story.change.delta", {
      from: displayToken(projection.delta.base_value),
      to: displayToken(projection.delta.proposed_value),
    }))}</strong>
    <small>${escapeHtml(t("acceptance.story.change.owner", { owner: displayToken(projection.owner) }))}</small>
    <small>${escapeHtml(t("acceptance.story.change.wait"))}</small>
    <small>${escapeHtml(t("acceptance.story.change.metrics", projection.metrics))}</small>
    <em>${escapeHtml(t("acceptance.story.change.safety", projection.metrics))}</em>
  </article>`;
}

function renderAcceptanceStory(state = {}) {
  const statusNode = byId("acceptance-story-status");
  const stepsNode = byId("acceptance-story-steps");
  const changesNode = byId("acceptance-change-summary");
  const finalNode = byId("acceptance-final-output");
  const quoteButton = byId("acceptance-download-quote");
  const evidenceButton = byId("acceptance-download-evidence");
  if (!statusNode || !stepsNode || !changesNode || !finalNode) return;

  if (state.schema_version === "orgrebase.workspace-state.v2") {
    const projection = currentRunValueProjection(state);
    const baseline = projection.status === "BASELINE_VERIFIED";
    const complete = projection.status === "PASS" || baseline;
    statusNode.textContent = t(baseline ? "value.current.baseline.detail" : complete ? "acceptance.story.currentComplete" : "acceptance.story.currentPending", { count: projection.receiptCount });
    statusNode.dataset.state = complete ? "complete" : "waiting";
    stepsNode.innerHTML = "";
    changesNode.innerHTML = "";
    const quote = state.quote;
    finalNode.innerHTML = quote ? `<article class="acceptance-final-card"><span>${escapeHtml(t("acceptance.story.final"))}</span><strong>${escapeHtml(quoteRevisionLabel(quote.version))}</strong><small>${escapeHtml(businessChangeObjectLabel(quote.id))}</small></article>` : "";
    if (quoteButton) quoteButton.disabled = !complete;
    if (evidenceButton) evidenceButton.disabled = !complete;
    return;
  }
  const taskIntake = state.task_intake || {};
  const taskConfirmed = Boolean(
    taskIntake.confirmation_receipt
      && taskIntake.confirmation_receipt.status === "ADMITTED_FOR_FORMATION",
  );
  const competition = activeCompetition(state);
  const collaboration = competition && competition.agent_collaboration || {};
  const plan = collaboration.orchestration_plan || {};
  const collaborationComplete = Boolean(
    taskConfirmed
      && state.formation
      && competition
      && plan.status === "COMPLETED"
      && String(competition.project_terminal_state || "").toUpperCase() === "COMPLETED",
  );
  const changes = state.changes || {};
  const launch = changeProjection("launch_date", changes.launch_date);
  const currency = changeProjection("currency", changes.currency);
  const launchComplete = Boolean(
    launch.receiptComplete && launch.approvalDigest && launch.reviewWaitSatisfied && launch.outcomeQuote,
  );
  const currencyComplete = Boolean(
    currency.receiptComplete && currency.approvalDigest && currency.reviewWaitSatisfied && currency.outcomeQuote,
  );
  const runProjection = currentRunValueProjection(state);
  const complete = Boolean(
    runProjection.status === "PASS"
      && taskConfirmed
      && collaborationComplete
      && launchComplete
      && currencyComplete,
  );

  statusNode.textContent = t(complete ? "acceptance.story.complete" : "acceptance.story.waiting");
  statusNode.dataset.state = complete ? "complete" : "waiting";
  stepsNode.innerHTML = [
    acceptanceStoryStep(1, "acceptance.story.step.employee", "acceptance.story.step.employeeDetail", taskConfirmed),
    acceptanceStoryStep(2, "acceptance.story.step.auto", "acceptance.story.step.autoDetail", collaborationComplete),
    acceptanceStoryStep(3, "acceptance.story.step.product", "acceptance.story.step.productDetail", launchComplete),
    acceptanceStoryStep(4, "acceptance.story.step.finance", "acceptance.story.step.financeDetail", currencyComplete),
    acceptanceStoryStep(5, "acceptance.story.step.apply", "acceptance.story.step.applyDetail", complete),
    `<aside class="acceptance-candidate-boundary">
      <strong>${escapeHtml(t("acceptance.story.candidateBoundary"))}</strong>
      <ul>
        <li>${escapeHtml(t("acceptance.story.candidateEmployee"))}</li>
        <li>${escapeHtml(t("acceptance.story.candidateDomain"))}</li>
        <li>${escapeHtml(t("acceptance.story.candidateCanonical"))}</li>
        <li>${escapeHtml(t("acceptance.story.candidateSkill"))}</li>
      </ul>
    </aside>`,
  ].join("");

  if (complete) {
    changesNode.innerHTML = [
      acceptanceChangeCard(launch, "acceptance.story.change.launch"),
      acceptanceChangeCard(currency, "acceptance.story.change.currency"),
    ].join("");
    const quote = state.quote || {};
    const payload = quote.payload || {};
    finalNode.innerHTML = `<article class="acceptance-final-card observed">
      <span>${escapeHtml(t("acceptance.story.final"))}</span>
      <strong>${escapeHtml(t("acceptance.story.finalSummary", {
        quote: quoteRevisionLabel(quote.version),
        launch: payload.launch_date || displayToken("NOT_OBSERVED"),
        currency: displayToken(payload.currency || "NOT_OBSERVED"),
      }))}</strong>
    </article>`;
  } else {
    changesNode.innerHTML = `<article class="acceptance-change-card pending"><strong>${escapeHtml(t("acceptance.story.pending"))}</strong><small>${escapeHtml(t("acceptance.story.change.waiting"))}</small></article>`;
    finalNode.innerHTML = `<article class="acceptance-final-card pending"><span>${escapeHtml(t("acceptance.story.final"))}</span><strong>${escapeHtml(t("acceptance.story.finalWaiting"))}</strong></article>`;
  }
  if (quoteButton) quoteButton.disabled = !complete;
  if (evidenceButton) evidenceButton.disabled = !complete;
}

function renderValueAndResponsibility(evidence, state = currentState || {}) {
  const passed = evidence && evidence.status === "PASS";
  const value = passed ? (evidence.value_and_responsibility || {}) : {};
  const competition = activeCompetition(state);
  if (value.primary_user) localizedText("value-primary-user", value.primary_user);
  else text("value-primary-user", t("value.primaryUnavailable"));
  text("value-process-status", passed
    ? activeCompetition(state)
      ? t("value.process.status", { count: (value.process_steps || []).length })
      : displayToken(value.process_status)
    : displayToken("UNAVAILABLE"));
  text("value-process-boundary", passed
    ? t("value.boundary.valid", { deliverable: displayToken(value.primary_deliverable) })
    : t("value.boundary.invalid"));

  const steps = Array.isArray(value.process_steps) ? value.process_steps : [];
  byId("value-process-steps").innerHTML = steps.length ? steps.map((step, index) => {
    const target = step.target_response || {};
    const processLabel = PROCESS_LABEL_KEYS[step.id] ? t(PROCESS_LABEL_KEYS[step.id]) : step.label;
    const toBeResponsible = Array.isArray(step.to_be_responsible) && step.to_be_responsible.length
      ? step.to_be_responsible
      : TO_BE_RESPONSIBLE_BY_PROCESS_STEP[step.id] || [];
    return `<tr>
      <td>${index + 1}</td>
      <td title="${escapeHtml(processLabel)}">${escapeHtml(processLabel)}${step.id === "quote-step:delivery-acceptance"
        ? `<small class="process-step-boundary">${escapeHtml(t("value.process.deliveryBoundary"))}</small>`
        : ""}</td>
      <td>${localizedFactHtml(step.as_is_interface_class)}</td>
      <td>${(step.responsible || []).map((role) => localizedFactHtml(role)).join(" + ")}</td>
      <td>${toBeResponsible.map((role) => localizedFactHtml(role)).join(" + ") || "—"}</td>
      <td>${localizedFactHtml(step.accountable)}</td>
      <td>${escapeHtml(`${target.value} ${displayToken(target.unit)}`)} · ${escapeHtml(t("value.target.design"))}</td>
      <td>${escapeHtml(
        step.named_connector_status === "NOT_RUN"
          ? t("value.connector.enterprisePending")
          : t("value.connector.controlledLocal")
      )}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="8">${escapeHtml(t("value.process.unavailable"))}</td></tr>`;

  renderCurrentRunValue(state, currentRetainedEvidence);
  renderAcceptanceStory(state);
}

function retainedWorkerBinding(bindings, domain) {
  const candidates = bindings.filter((binding) => String(binding.domain || "").toLowerCase() === domain);
  return [...candidates].reverse().find((binding) => binding.status === "completed")
    || candidates[candidates.length - 1]
    || null;
}

function renderFormationTopology(formation, passed) {
  const card = byId("formation-proof-card");
  card.hidden = false;
  if (!passed) {
    text("formation-proof-summary", t("formationProof.unavailable"));
    text("formation-proof-boundary", t("formationProof.unavailable"));
    byId("formation-agent-topology").innerHTML = "";
    byId("formation-proof-boundary").removeAttribute("title");
    return;
  }
  const domainTasks = Array.isArray(formation.domain_tasks) ? formation.domain_tasks : [];
  const reviewer = formation.reviewer_barrier || {};
  const plannedDomains = Array.isArray(formation.planned_domain_ids) ? formation.planned_domain_ids : [];
  const actualDomains = Array.isArray(formation.actual_agentteams_domain_ids) ? formation.actual_agentteams_domain_ids : [];
  text("formation-proof-summary", t("formationProof.match", {
    planned: formation.planned_domain_count,
    actual: formation.actual_agentteams_domain_count,
    reviewers: reviewer.task_id ? 1 : 0,
    actions: formation.agentteams_action_count,
  }));
  text("formation-proof-boundary", t("formationProof.boundary", {
    domains: plannedDomains.map((domain) => displayToken(String(domain).toUpperCase())).join(" + "),
  }));
  byId("formation-proof-boundary").title = [
    formation.run_id,
    formation.receipt_digest,
    formation.verification_digest,
  ].filter(Boolean).join(" · ");
  const planNode = {
    actorKind: "control",
    role: t("formationProof.planRole"),
    name: t("formationProof.planName"),
    status: t("formationProof.planStatus", {
      planned: formation.planned_domain_count,
      actual: formation.actual_agentteams_domain_count,
    }),
    tone: "control",
    dependsOn: [],
    inputRefs: [formation.run_id, formation.context_envelope_digest].filter(Boolean),
    inputDigest: formation.execution_plan_digest,
    digest: formation.formation_receipt_digest,
    outputDigest: formation.coalition_plan_digest,
    model: "NONE",
    tool: "AgentTeams TeamHarness",
    skill: "NONE",
    trace: "NOT_OBSERVED",
    candidateOnly: formation.candidate_only,
    writes: formation.canonical_target_writes,
    evidenceMode: formation.verification_strength,
  };
  const workerNodes = domainTasks.map((task) => ({
    actorKind: "agent",
    role: t("formationProof.workerRole", { domain: displayToken(String(task.domain_id || "DOMAIN").toUpperCase()) }),
    name: task.assignee_actor_id,
    status: task.terminal_status,
    tone: task.terminal_status === "completed" ? "pass" : "not-run",
    dependsOn: [],
    inputRefs: [formation.context_envelope_digest].filter(Boolean),
    inputDigest: formation.context_envelope_digest,
    digest: task.digest,
    outputDigest: task.digest,
    model: "NOT_OBSERVED",
    tool: t("lifecycle.title"),
    skill: "NOT_OBSERVED",
    trace: "NOT_OBSERVED",
    candidateOnly: formation.candidate_only,
    writes: 0,
    evidenceMode: formation.verification_strength,
  }));
  const reviewerNode = {
    actorKind: "agent",
    role: t("formationProof.reviewerRole"),
    name: t("formationProof.reviewerName"),
    exactIdentifier: reviewer.assignee_actor_id,
    status: reviewer.terminal_status,
    tone: reviewer.terminal_status === "completed" ? "pass" : "not-run",
    dependsOn: Array.isArray(reviewer.depends_on) ? reviewer.depends_on : [],
    inputRefs: domainTasks.map((task) => task.digest).filter(Boolean),
    inputDigest: formation.coalition_plan_digest,
    digest: reviewer.digest,
    outputDigest: reviewer.digest,
    model: "NOT_OBSERVED",
    tool: t("formationProof.reviewerRole"),
    skill: "NOT_OBSERVED",
    trace: "NOT_OBSERVED",
    candidateOnly: formation.candidate_only,
    writes: formation.canonical_target_writes,
    evidenceMode: formation.verification_strength,
  };
  renderDirectedTopology("formation-agent-topology", [
    { label: t("topology.stage.coordinate"), className: "leader-stage", nodes: [planNode] },
    { label: t("topology.stage.domainWorkers"), className: "workers-stage", nodes: workerNodes },
    { label: t("topology.stage.accept"), className: "control-stage", nodes: [reviewerNode] },
  ]);
  byId("formation-agent-topology").dataset.plannedDomains = plannedDomains.join(",");
  byId("formation-agent-topology").dataset.actualDomains = actualDomains.join(",");
}

function renderRetainedTopology(evidence, collaboration, passed) {
  const bindings = passed && Array.isArray(collaboration.bindings) ? collaboration.bindings : [];
  const spine = passed && Array.isArray(evidence.evidence_spine) ? evidence.evidence_spine : [];
  const agentteamsReceipt = spine.find((item) => item.stage === "AGENTTEAMS") || {};
  const candidateReceipt = spine.find((item) => item.stage === "CANDIDATE") || {};
  const skillReceipt = spine.find((item) => item.stage === "SKILL") || {};
  const toolReceipt = spine.find((item) => item.stage === "TOOL") || {};
  const authority = collaboration.authority || {};
  const successor = collaboration.governed_successor || {};
  const workers = DOMAIN_WORKERS.map(({ domain, label, actor }) => {
    const binding = retainedWorkerBinding(bindings, domain);
    return {
      actorKind: "agent",
      role: t("topology.role.workerGeneric", { domain: displayToken(label) }),
      name: (binding && binding.assignee) || actor,
      status: binding ? `${binding.status} · attempt ${observed(binding.attempt)}` : "NO BINDING OBSERVED",
      tone: binding && binding.status === "completed" ? "pass" : "not-run",
      dependsOn: binding && binding.predecessor_task_id,
      inputRefs: binding && binding.context_projection_digest ? [binding.context_projection_digest] : [],
      inputDigest: binding && binding.context_projection_digest,
      digest: binding && (binding.control_decision_ref || binding.task_digest),
      outputDigest: binding && (binding.observed_result_digest || binding.output_digest || binding.deliverable_digest),
      model: binding && binding.model_version,
      tool: binding && binding.tool_versions,
      skill: binding && binding.skill_versions,
      trace: binding && binding.trace_id,
      candidateOnly: binding && binding.candidate_only,
      writes: binding && binding.target_writes,
      evidenceMode: binding && binding.evidence_class,
    };
  });
  const coordinator = {
    actorKind: "control",
    role: t("topology.role.projectBoundary"),
    name: t("topology.name.quoteTaskCoordinator"),
    exactIdentifier: collaboration.project_id,
    status: passed ? `PROJECT ${observed(collaboration.terminal_state)}` : "FAIL CLOSED",
    tone: passed ? "pass" : "not-run",
    dependsOn: [],
    inputRefs: [],
    inputDigest: "NOT_OBSERVED",
    digest: agentteamsReceipt.receipt_digest,
    outputDigest: agentteamsReceipt.receipt_digest,
    model: "NOT_OBSERVED",
    tool: t("lifecycle.title"),
    skill: "NOT_OBSERVED AT PROJECT NODE",
    trace: "NOT_OBSERVED",
    candidateOnly: true,
    writes: authority.candidate_target_writes ?? 0,
    evidenceMode: agentteamsReceipt.evidence_class || evidence.evidence_lane,
  };
  const control = {
    actorKind: "control",
    role: t("topology.role.controlAcceptance"),
    name: t("topology.name.acceptance"),
    status: passed ? observed(candidateReceipt.status) : "FAIL CLOSED",
    tone: passed ? "control" : "not-run",
    dependsOn: bindings.map((binding) => binding.task_id).filter(Boolean),
    inputRefs: bindings.map((binding) => binding.control_decision_ref).filter(Boolean),
    inputDigest: agentteamsReceipt.receipt_digest,
    digest: candidateReceipt.receipt_digest,
    outputDigest: candidateReceipt.receipt_digest,
    model: t("topology.detail.deterministic"),
    tool: toolReceipt.status ? `CONTROLLED HTTP TOOL · ${toolReceipt.status}` : "NOT_OBSERVED",
    skill: skillReceipt.status ? `enterprise-quote-compose · ${skillReceipt.status}` : "NOT_OBSERVED",
    trace: "OTLP CORRELATED · TRACE ID NOT PROJECTED",
    candidateOnly: true,
    writes: candidateReceipt.target_writes ?? 0,
    evidenceMode: candidateReceipt.evidence_class,
  };
  const human = {
    actorKind: "human",
    role: t("topology.role.humanCommand"),
    name: t("topology.name.localCommands"),
    status: successor.approval_input_mode || "NOT_OBSERVED",
    tone: successor.approval_input_mode ? "human" : "not-run",
    dependsOn: candidateReceipt.receipt_digest ? [candidateReceipt.receipt_digest] : [],
    inputRefs: [],
    inputDigest: "NOT_OBSERVED",
    digest: "NOT_OBSERVED",
    outputDigest: "NOT_OBSERVED",
    model: "NONE",
    tool: t("topology.detail.localCommand"),
    skill: "NONE",
    trace: "NOT_OBSERVED",
    candidateOnly: "N/A",
    writes: 0,
    evidenceMode: "NOT EXTERNAL HUMAN VALIDATION",
  };
  const store = {
    actorKind: "store",
    role: t("topology.role.canonicalState"),
    name: authority.canonical_state || "StateStore / RebaseWorkflow",
    status: successor.terminal_state || "NOT_OBSERVED",
    tone: successor.terminal_state ? "store" : "not-run",
    dependsOn: ["scripted local domain-owner commands"],
    inputRefs: [],
    inputDigest: "NOT_OBSERVED",
    digest: "NOT_OBSERVED",
    outputDigest: successor.final_quote_ref,
    model: t("topology.detail.deterministic"),
    tool: "StateStore + RebaseWorkflow",
    skill: "enterprise-quote-compose UPSTREAM OF CANDIDATE",
    trace: "OTLP CORRELATED · TRACE ID NOT PROJECTED",
    candidateOnly: false,
    writes: successor.canonical_target_writes ?? "NOT_OBSERVED",
    evidenceMode: "CONTROLLED_LOCAL GOVERNED SUCCESSOR",
  };
  text("retained-topology-boundary", t("retained.boundary.value", { runId: evidence.run_id || displayToken("UNAVAILABLE") }));
  exactAuditTitle("retained-topology-boundary", evidence.run_id);
  renderDirectedTopology("retained-agent-topology", [
    { label: t("topology.stage.coordinate"), className: "leader-stage", nodes: [coordinator] },
    { label: t("topology.stage.domainWorkers"), className: "workers-stage", nodes: workers },
    { label: t("topology.stage.accept"), className: "control-stage", nodes: [control] },
    { label: t("topology.stage.humanShort"), className: "human-stage", nodes: [human] },
    { label: t("topology.stage.write"), className: "store-stage", nodes: [store] },
  ]);
}

function renderMechanismComparison(evidence) {
  const body = byId("mechanism-comparison-rows");
  if (!body) return;
  body.replaceChildren();
  text("mechanism-comparison-source", "—");
  for (const key of ["path", "quote-digest", "registry-digest"]) text(`mechanism-comparison-${key}`, "—");
  const view = evidence?.status === "PASS" ? evidence.mechanism_comparison : null;
  const rows = view?.rows;
  const valid = view?.status === "PASS" && view.implementation === "ReferenceWorkspaceBenchmarkSUT"
    && view.current_task_run === false && view.packaged_product_comparison === false
    && view.live_model_comparison === false && view.evidence_class === "SYNTHETIC_GOLD"
    && view.benchmark_version === "OWB v1.1" && isSha256Digest(view.source_digest)
    && view.source_path === "evidence/workspace/latest/evaluation-suite.json"
    && isSha256Digest(view.quote_value_digest) && isSha256Digest(view.registry_digest)
    && Array.isArray(rows) && rows.length === 13 && new Set(rows.map((row) => row?.profile)).size === 13
    && rows.every((row) => row && typeof row.profile === "string" && row.case_count === 192
      && ["reference", "baselines", "ablations"].includes(row.kind)
      && Number.isFinite(row.score) && row.score >= 0 && row.score <= 100 && row.threshold === 90
      && ["PASS", "FAIL"].includes(row.hard_gate_status) && ["PASS", "FAIL"].includes(row.status)
      && Array.isArray(row.failed_gates) && row.failed_gates.every((gate) => typeof gate === "string")
      && row.hard_gate_status === (row.failed_gates.length ? "FAIL" : "PASS")
      && row.status === (row.score >= row.threshold && !row.failed_gates.length ? "PASS" : "FAIL")
      && isSha256Digest(row.report_digest));
  text("mechanism-comparison-status", t(valid ? "mechanism.ready" : "mechanism.unavailable"));
  byId("mechanism-comparison-results").hidden = !valid;
  if (!valid) return;
  text("mechanism-comparison-source", view.source_digest);
  text("mechanism-comparison-path", view.source_path);
  text("mechanism-comparison-quote-digest", view.quote_value_digest);
  text("mechanism-comparison-registry-digest", view.registry_digest);
  for (const row of rows) {
    const tr = document.createElement("tr");
    const profile = document.createElement("th");
    profile.scope = "row";
    const name = document.createElement("strong");
    name.textContent = t(`mechanism.strategy.${row.profile}`);
    const kind = document.createElement("small");
    kind.textContent = `${t(`mechanism.${row.kind}`)} · ${row.profile}`;
    profile.append(name, kind);
    tr.append(profile);
    for (const value of [String(row.score), row.hard_gate_status, row.status]) {
      const td = document.createElement("td");
      td.textContent = value;
      if (value === "FAIL") td.classList.add("mechanism-gate-failed");
      tr.append(td);
    }
    if (row.failed_gates.length) {
      const detail = document.createElement("small");
      detail.textContent = row.failed_gates.join(" · ");
      tr.children[2].append(detail);
    }
    body.append(tr);
  }
}

function renderRetainedEvidence(evidence) {
  window.OrgRebaseArchiveRevalidation?.render(evidence?.archive_revalidation, currentLanguage);
  renderMechanismComparison(evidence);
  currentRetainedEvidence = evidence;
  const passed = evidence && evidence.status === "PASS";
  const collaboration = (evidence && evidence.agent_collaboration) || {};
  text("retained-run-id", passed ? `${t("retained.runLabel")} · ${shortDigest(evidence.run_id, 42)}` : t("retained.unavailable"));
  text(
    "retained-archive-time",
    passed && isSha256Digest(evidence.archive_identity_digest)
      ? shortDigest(evidence.archive_identity_digest, 34)
      : "—",
  );
  exactAuditTitle("retained-run-id", passed ? evidence.run_id : null);
  exactAuditTitle(
    "retained-archive-time",
    passed && isSha256Digest(evidence.archive_identity_digest) ? evidence.archive_identity_digest : null,
  );
  renderRetainedTopology(evidence || {}, collaboration, passed);
  const spine = passed && Array.isArray(evidence.evidence_spine) ? evidence.evidence_spine : [];
  byId("retained-evidence-spine").innerHTML = spine.length
    ? spine.map((item) => spineNode(
      `${item.order}. ${item.stage}`,
      item.status,
      `${t("enum.writes", { count: item.target_writes })} · ${shortDigest(item.receipt_digest, 11)}`,
      item.stage === "GOVERNED_APPLY" ? "human" : "pass",
    )).join("")
    : spineNode("RETAINED", "UNAVAILABLE", t("spine.detail.validationFailed"), "not-run");

  const bindings = passed && Array.isArray(collaboration.bindings) ? collaboration.bindings : [];

  const formation = collaboration.formation_taskflow || {};
  const plannedDomains = passed && Array.isArray(formation.planned_domain_ids) ? formation.planned_domain_ids : [];
  const actualDomains = passed && Array.isArray(formation.actual_agentteams_domain_ids) ? formation.actual_agentteams_domain_ids : [];
  const formationDomainTasks = Array.isArray(formation.domain_tasks) ? formation.domain_tasks : [];
  const formationReviewer = formation.reviewer_barrier || {};
  const reviewerDependencies = Array.isArray(formationReviewer.depends_on) ? formationReviewer.depends_on : [];
  const domainTaskIds = formationDomainTasks.map((item) => item.task_id);
  const formationPassed = passed
    && formation.status === "PASS"
    && formation.topology_match === true
    && formation.planned_domain_count === plannedDomains.length
    && formation.actual_agentteams_domain_count === actualDomains.length
    && plannedDomains.length > 0
    && plannedDomains.length === actualDomains.length
    && plannedDomains.every((domain, index) => domain === actualDomains[index])
    && formationDomainTasks.length === plannedDomains.length
    && reviewerDependencies.length === domainTaskIds.length
    && reviewerDependencies.every((taskId, index) => taskId === domainTaskIds[index])
    && formation.candidate_only === true
    && formation.canonical_target_writes === 0;
  renderFormationTopology(formation, formationPassed);
  const retainedActions = passed && Array.isArray(collaboration.actions) ? collaboration.actions : [];
  const lifecycle = passed && Array.isArray(collaboration.lifecycle) ? collaboration.lifecycle : [];
  const lifecycleNodes = lifecycle.length
    ? lifecycle.map((item, index) => {
      const action = retainedActions.find((candidate) => (
        candidate.action === item.evidence
          && candidate.ok === true
          && Boolean(candidate.digest)
      ));
      const handoffBinding = item.stage === "HANDOFF_BINDING"
        ? bindings.find((binding) => Boolean(binding.context_projection_digest || binding.digest))
        : null;
      const observedDigest = action && action.digest
        || handoffBinding && (handoffBinding.context_projection_digest || handoffBinding.digest);
      return spineNode(
        `${index + 1}. ${displayToken(item.stage)}`,
        observedDigest ? "PASS" : "NOT_OBSERVED",
        observedDigest ? `${item.evidence} · ${shortDigest(observedDigest, 11)}` : item.evidence,
        observedDigest ? "pass" : "not-run",
      );
    })
    : [spineNode("LIFECYCLE", "UNAVAILABLE", t("spine.detail.failClosed"), "not-run")];
  byId("retained-lifecycle").innerHTML = lifecycleNodes.join("");
  const authority = collaboration.authority || {};
  if (passed) byId("retained-authority").innerHTML = t("retained.authority.value", { state: localizedFactHtml(authority.canonical_state) });
  else localizedText("retained-authority", "UNAVAILABLE");
  const runBoundary = collaboration.run_separation || {};
  if (passed) byId("retained-run-separation").innerHTML = `${localizedFactHtml(runBoundary.active_pack_pilot)} · ${localizedFactHtml(runBoundary.retained_governed_successor)}`;
  else localizedText("retained-run-separation", "UNAVAILABLE");
  const exceptions = collaboration.exception_semantics || {};
  const compensation = collaboration.compensation_evidence || {};
  if (passed) byId("retained-exception-semantics").innerHTML = t("retained.exceptions.value", {
    conflict: localizedFactHtml(exceptions.result_conflict),
    reassign: localizedFactHtml(exceptions.reassignment),
    late: localizedFactHtml(exceptions.late_result_fencing),
    atomic: localizedFactHtml(exceptions.canonical_transaction_atomic_rollback),
    downstream: localizedFactHtml(exceptions.downstream_state_compensation),
    git: localizedFactHtml(exceptions.reversible_external_git_compensation),
    enterprise: exceptions.real_enterprise_connector_compensation === "NOT_RUN"
      ? escapeHtml(t("retained.exceptions.enterprisePending"))
      : localizedFactHtml(exceptions.real_enterprise_connector_compensation),
    residuals: localizedFactHtml(compensation.git_residual_effect_count),
    writes: localizedFactHtml(compensation.read_model_target_writes),
  });
  else localizedText("retained-exception-semantics", "UNAVAILABLE");
  const successor = collaboration.governed_successor || {};
  if (passed) byId("retained-governed-successor").innerHTML = t("retained.successor.value", {
    quote: escapeHtml(successor.final_quote_ref),
    terminal: localizedFactHtml(successor.terminal_state),
    approval: localizedFactHtml(successor.approval_input_mode),
    writes: escapeHtml(successor.canonical_target_writes),
  });
  else localizedText("retained-governed-successor", "UNAVAILABLE");

  const skills = passed && Array.isArray(evidence.skills) ? evidence.skills : [];
  byId("retained-skills").innerHTML = skills.length ? skills.map((skill) => `<article class="binding-card">
    <span>${t("retained.skill.header", { version: escapeHtml(skill.version), verdict: localizedFactHtml(skill.verdict) })}</span>
    <strong>${escapeHtml(skillDisplayName(skill.name))}</strong>
    <small class="skill-stable-id">${escapeHtml(t("skill.display.stableId", { id: skill.name }))}</small>
    <small>${t("retained.skill.evaluation", { cases: escapeHtml(skill.case_count), passed: escapeHtml(skill.gates_passed), total: escapeHtml(skill.gate_count), mode: localizedFactHtml(skill.resource_mode) })}</small>
    <small>${(skill.lifecycle || []).map((stateName) => localizedFactHtml(stateName)).join("→")}</small>
    <small>${t("retained.skill.requal", { verdict: localizedFactHtml(skill.requalification_verdict), state: localizedFactHtml(skill.rollback_state), mode: localizedFactHtml(skill.rollback_mode) })}</small>
    ${skill.invocation && skill.invocation.status === "SUCCESS" ? `<small>${t("retained.skill.invocation")}</small>` : ""}
    <details class="skill-technical-details"><summary>${escapeHtml(t("retained.skill.technical"))}<b aria-hidden="true">＋</b></summary>
      <small title="${escapeHtml(skill.release_head || "")}">release · ${escapeHtml(shortDigest(skill.release_head, 18))}</small>
      ${skill.invocation && skill.invocation.receipt_digest ? `<small title="${escapeHtml(skill.invocation.receipt_digest)}">invocation · ${escapeHtml(shortDigest(skill.invocation.receipt_digest, 18))}</small>` : ""}
    </details>
  </article>`).join("") : `<p class="capability-empty">${escapeHtml(t("retained.skill.unavailable"))}</p>`;

  const actions = passed && Array.isArray(collaboration.actions) ? collaboration.actions : [];
  text("retained-ledger-count", passed ? t("retained.ledger.count", { actions: actions.length, bindings: bindings.length }) : t("retained.ledger.fail"));
  byId("retained-action-ledger").innerHTML = actions.length ? actions.map((action) => `<tr>
    <td>${escapeHtml(action.sequence)}</td>
    <td>${escapeHtml(action.tool)}</td>
    <td title="${escapeHtml(action.key)}">${localizedFactHtml(action.action)}</td>
    <td>${localizedFactHtml(action.status)}${action.ok ? "" : ` · ${localizedFactHtml("REJECTED")}`}</td>
    <td title="${escapeHtml(action.digest)}">${escapeHtml(shortDigest(action.digest, 16))}</td>
  </tr>`).join("") : `<tr><td colspan="5">${escapeHtml(t("retained.empty"))}</td></tr>`;
  renderCurrentRunValue(currentState || UNAVAILABLE_WORKSPACE_PROJECTION, evidence);
  renderOperations(currentState || UNAVAILABLE_WORKSPACE_PROJECTION);
}

function operationReceiptCard(label, status, detail, digest) {
  const receipt = digest ? ` · <span class="machine-token">${escapeHtml(t("operations.card.receiptBound"))}</span>` : "";
  return `<article class="ops-receipt"><span>${escapeHtml(label)}</span><strong>${localizedFactHtml(status)}</strong><small>${escapeHtml(detail)}${receipt}</small></article>`;
}

function operationProofLane(label, items) {
  return `<section class="ops-proof-lane"><header>${escapeHtml(label)}</header><div class="ops-proof-nodes">${items.map((item) => spineNode(
    `${item.order}. ${displayToken(item.stage)}`,
    item.status,
    item.detail || `${Number.isInteger(item.target_writes)
      ? t("operations.sameRun.externalTargetWrites", { count: item.target_writes })
      : item.stage === "CONTROL" && item.status !== "WAITING"
        ? `${t("operations.sameRun.internalCanonicalWrite")} · ${t("operations.sameRun.externalTargetWritesUnobserved")}`
        : t("operations.sameRun.externalTargetWritesUnobserved")} · ${item.receipt_digest ? t("operations.card.receiptBound") : displayToken("NOT_OBSERVED")}`,
    item.tone || (item.stage === "GOVERNED_APPLY" ? "human" : "pass"),
  )).join("")}</div></section>`;
}

function nativeAgentTeamsFormationReceipt(state, rows, execution) {
  const competition = state && state.competition_evidence || {};
  if (competition.status !== "PASS" || competition.run_id !== execution.run_id) return null;
  const sameRunRows = Array.isArray(rows)
    ? rows.filter((row) => row.run_id === execution.run_id && row.source_run_id === execution.run_id)
    : [];
  const hasReceipt = (row) => Boolean(row && row.receipt_digest);
  const collaboration = competition.agent_collaboration || {};
  const plan = collaboration.orchestration_plan || {};
  const planTasks = Array.isArray(plan.tasks) ? plan.tasks : [];
  const finalReviewerTask = [...planTasks].reverse().find((task) => (
    task.role === "REVIEWER"
      && task.candidate_only === true
      && Number(task.target_writes) === 0
      && task.binding_digest
      && task.digest
  )) || null;
  const finalDependencyIds = finalReviewerTask && Array.isArray(finalReviewerTask.depends_on)
    ? finalReviewerTask.depends_on
    : [];
  const trustedTaskRow = (taskId, plane) => sameRunRows.find((row) => (
    row.plane === plane
      && row.action === "EXECUTE_NATIVE_TASKFLOW_CANDIDATE"
      && row.permission === "CANDIDATE_ONLY"
      && Number(row.target_writes) === 0
      && row.source_ref === taskId
      && String(row.status || "").toUpperCase() === "TRUSTED_COMPLETE"
      && hasReceipt(row)
  )) || null;
  const managerTask = planTasks.find((task) => (
    task.id === plan.project_id
      && task.candidate_only === true
      && Number(task.target_writes) === 0
      && task.digest
  )) || null;
  const managerRow = managerTask && trustedTaskRow(managerTask.id, "AGENTTEAMS");
  const dependencyTasksBound = finalDependencyIds.length > 0 && finalDependencyIds.every((taskId) => {
    const task = planTasks.find((candidate) => candidate.id === taskId);
    return Boolean(
      task
        && task.role === "DOMAIN_WORKER"
        && task.candidate_only === true
        && Number(task.target_writes) === 0
        && task.binding_digest
        && task.digest
        && trustedTaskRow(taskId, "AGENTTEAMS"),
    );
  });
  // AgentTeams creates and executes the Reviewer task, while OrgRebase projects
  // that task lifecycle onto the REVIEWER plane so it cannot be mistaken for a
  // domain worker. The verdict below is a second, separately bound REVIEWER row.
  const finalReviewerTaskRow = finalReviewerTask && trustedTaskRow(finalReviewerTask.id, "REVIEWER");
  const finalReviewerAttemptRef = finalReviewerTask && Number.isInteger(finalReviewerTask.attempt)
    ? `reviewer:attempt-${finalReviewerTask.attempt}`
    : null;
  const finalReviewerPassRow = [...sameRunRows].reverse().find((row) => (
    row.plane === "REVIEWER"
      && row.action === "REPLAN_THEN_ACCEPT_EXACT_PROVENANCE"
      && row.permission === "CANDIDATE_ONLY"
      && Number(row.target_writes) === 0
      && finalReviewerAttemptRef
      && row.source_ref === finalReviewerAttemptRef
      && String(row.status || "").toUpperCase() === "PASS"
      && hasReceipt(row)
  )) || null;
  const finalReviewerVerdict = collaboration.reviewer && collaboration.reviewer.attempt_2 || {};
  const reviewerReceiptBound = Boolean(
    finalReviewerPassRow
      && finalReviewerVerdict.verdict === "PASS"
      && competition.summary_digest
      && finalReviewerPassRow.receipt_digest === competition.summary_digest,
  );
  // A superseded ABSTAIN remains in the ledger. Completion follows the final
  // reviewer task's bound dependency set, never every historical attempt.
  return plan.run_id === execution.run_id
    && plan.status === "COMPLETED"
    && plan.digest
    && managerRow
    && dependencyTasksBound
    && finalReviewerTaskRow
    && reviewerReceiptBound
    ? finalReviewerPassRow
    : null;
}

const OTLP_AUDIT_FIELD_LABELS = Object.freeze({
  "deployment.environment.name": "operations.audit.field.deploymentEnvironment",
  "service.name": "operations.audit.field.serviceName",
  "service.version": "operations.audit.field.serviceVersion",
  "orgrebase.workflow.run_id": "operations.audit.field.runId",
  "orgrebase.agentteams.task.id": "operations.audit.field.taskId",
  "orgrebase.skill.name": "operations.audit.field.skillName",
  "orgrebase.tool.receipt.digest": "operations.audit.field.toolReceipt",
  "orgrebase.receipt.digest": "operations.audit.field.receipt",
  "orgrebase.target.write.count": "operations.audit.field.targetWrites",
});

const OTLP_SIGNAL_LABELS = Object.freeze({
  traces: "operations.audit.signal.traces",
  logs: "operations.audit.signal.logs",
  metrics: "operations.audit.signal.metrics",
});

const ENTERPRISE_CONNECTOR_LABELS = Object.freeze({
  crm: "operations.audit.connector.crm",
  cpq: "operations.audit.connector.cpq",
  clm: "operations.audit.connector.clm",
  erp: "operations.audit.connector.erp",
  knowledge_base: "operations.audit.connector.knowledgeBase",
});

function operationAuditFact(label, valueHtml) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${valueHtml}</dd></div>`;
}

function operationAuditSection(title, body) {
  return `<section class="ops-audit-group"><h4>${escapeHtml(title)}</h4>${body}</section>`;
}

function operationAuditTokenList(values, labels) {
  return `<ul class="ops-audit-token-list">${values.map((value) => `<li><span>${escapeHtml(t(labels[value] || value))}</span><code>${escapeHtml(value)}</code></li>`).join("")}</ul>`;
}

function retentionPeriod(seconds) {
  const exactSeconds = Number(seconds);
  if (!Number.isInteger(exactSeconds) || exactSeconds <= 0) {
    return localizedFactHtml("NOT_OBSERVED", { showRaw: true });
  }
  const days = exactSeconds / 86400;
  const displayedDays = Number.isInteger(days) ? days : Number(days.toFixed(3));
  return escapeHtml(t("operations.audit.retentionPeriod", {
    days: displayedDays,
    seconds: exactSeconds,
  }));
}

function renderOperationsAudit(evidence, operations, proofVerified) {
  const target = byId("ops-audit-content");
  if (evidence.status !== "PASS") {
    target.innerHTML = `<p class="ops-audit-unavailable">${escapeHtml(t("operations.audit.unavailable"))}</p>`;
    return;
  }

  const connectors = operations.connectors || {};
  const source = connectors.source || {};
  const tool = connectors.tool || {};
  const enterprise = connectors.enterprise || {};
  const otlp = operations.otlp || {};
  const retention = operations.retention || {};
  const readiness = operations.enterprise_readiness || {};
  const signals = Array.isArray(otlp.signals) ? otlp.signals : [];
  const fields = Array.isArray(otlp.key_fields) ? otlp.key_fields : [];
  const connectorFacts = [
    operationAuditFact(
      t("operations.audit.connector.source"),
      `${escapeHtml(`HTTP ${source.http_status ?? "—"}`)} · ${localizedFactHtml(source.status || "UNAVAILABLE")}`,
    ),
    operationAuditFact(
      t("operations.audit.connector.tool"),
      `${escapeHtml(`HTTP ${tool.http_status ?? "—"}`)} · ${localizedFactHtml(tool.status || "UNAVAILABLE")}`,
    ),
    ...Object.entries(ENTERPRISE_CONNECTOR_LABELS).map(([connectorId, labelKey]) => operationAuditFact(
      t(labelKey),
      enterprise[connectorId] === "NOT_RUN"
        ? escapeHtml(t("operations.audit.connector.pendingEnterprise"))
        : localizedFactHtml(enterprise[connectorId] ?? "UNAVAILABLE"),
    )),
  ].join("");
  const businessEvidenceUnaffected = retention.canonical_business_evidence_affected === false
    && retention.canonical_business_evidence_deleted === false;
  const productionReady = readiness.production_ready === true
    ? localizedFactHtml("TRUE")
    : readiness.production_ready === false
      ? escapeHtml(t("operations.audit.productionPilot"))
      : localizedFactHtml("UNAVAILABLE");
  const contractualSla = readiness.contractual_sla == null || readiness.contractual_sla === "NOT_RUN"
    ? escapeHtml(t("operations.readiness.enterpriseValidation"))
    : localizedFactHtml(readiness.contractual_sla);
  const geographicFailover = !readiness.geographic_failover || readiness.geographic_failover === "NOT_RUN"
    ? escapeHtml(t("operations.readiness.enterpriseValidation"))
    : localizedFactHtml(readiness.geographic_failover);

  target.innerHTML = `<div class="ops-audit-grid">
    ${operationAuditSection(t("operations.audit.run.title"), `<dl class="ops-audit-facts">
      ${operationAuditFact(t("operations.audit.chain"), proofVerified ? escapeHtml(t("operations.audit.chainPass")) : localizedFactHtml("UNAVAILABLE"))}
    </dl>`)}
    ${operationAuditSection(t("operations.audit.otlp.title"), `<dl class="ops-audit-facts">
      ${operationAuditFact(t("operations.audit.signals"), operationAuditTokenList(signals, OTLP_SIGNAL_LABELS))}
      ${operationAuditFact(t("operations.audit.fields", { count: fields.length }), operationAuditTokenList(fields, OTLP_AUDIT_FIELD_LABELS))}
    </dl>`)}
    ${operationAuditSection(t("operations.audit.connectors.title"), `<dl class="ops-audit-facts">${connectorFacts}</dl>`)}
    ${operationAuditSection(t("operations.audit.retention.title"), `<dl class="ops-audit-facts">
      ${operationAuditFact(t("operations.audit.indexedTelemetry"), retentionPeriod(retention.indexed_telemetry_seconds))}
      ${operationAuditFact(t("operations.audit.rejectedDiagnostics"), retentionPeriod(retention.rejected_payload_diagnostic_seconds))}
      ${operationAuditFact(t("operations.audit.businessEvidence"), businessEvidenceUnaffected ? escapeHtml(t("operations.audit.businessEvidenceUnaffected")) : localizedFactHtml("UNAVAILABLE"))}
      ${operationAuditFact(t("operations.audit.rejectedRaw"), retention.rejected_raw_payload_persisted === false ? escapeHtml(t("operations.audit.rawNotPersisted")) : localizedFactHtml("UNAVAILABLE"))}
    </dl>`)}
    ${operationAuditSection(t("operations.audit.readiness.title"), `<dl class="ops-audit-facts">
      ${operationAuditFact(t("operations.audit.deployment"), localizedFactHtml(readiness.deployment_profile || "UNAVAILABLE"))}
      ${operationAuditFact(t("operations.audit.productionReady"), productionReady)}
      ${operationAuditFact(t("operations.audit.contractualSla"), contractualSla)}
      ${operationAuditFact(t("operations.audit.geoDr"), geographicFailover)}
      ${operationAuditFact(t("operations.audit.sbom"), escapeHtml(`${readiness.sbom_format || "—"} · OrgRebase ${readiness.artifact_version || "—"} · ${readiness.sbom_component_count ?? "—"}`))}
      ${operationAuditFact(t("operations.audit.contributing"), localizedFactHtml(readiness.contributing_guide || "UNAVAILABLE"))}
    </dl>`)}
  </div><p class="ops-audit-boundary">${escapeHtml(t("operations.audit.localBoundary"))}</p>`;
}

function exactFrozenEvidenceIndex(evidence) {
  const index = evidence && evidence.evidence_index;
  const privacy = index && index.privacy;
  const evidenceClasses = index && index.evidence_classes;
  if (
    !evidence
      || evidence.status !== "PASS"
      || evidence.verification_status !== "PASS"
      || evidence.archive_status !== "FROZEN_HISTORICAL_VALIDATION"
      || evidence.current_task_run !== false
      || evidence.current_pack_pilot_run !== false
      || evidence.pack_pilot_binding !== "NOT_SAME_RUN"
      || !index
      || index.schema_version !== "orgrebase.semifinal-closure-evidence-index.v1"
      || index.status !== "PASS"
      || index.archive_status !== "FROZEN_HISTORICAL_VALIDATION"
      || index.current_task_run !== false
      || index.run_id !== evidence.run_id
      || !Number.isInteger(index.entry_count)
      || index.entry_count <= 0
      || !isSha256Digest(index.index_digest)
      || !isSha256Digest(index.pack_digest)
      || !Array.isArray(evidenceClasses)
      || evidenceClasses.length === 0
      || evidenceClasses.some((item) => typeof item !== "string" || !item)
      || new Set(evidenceClasses).size !== evidenceClasses.length
      || index.verifier_status !== "PASS"
      || index.target_writes !== 0
      || !privacy
      || privacy.canary_status !== "PASS"
      || privacy.canary_reason_code !== "PRIVACY_PAYLOAD_REJECTED"
      || privacy.rejection_reason_code !== "OTLP_RESTRICTED_FIELD:secret"
      || privacy.raw_payload_retained !== false
      || privacy.sensitive_values_disclosed !== false
  ) return null;
  return index;
}

function renderEvidenceIndexArchive(evidence) {
  const index = exactFrozenEvidenceIndex(evidence);
  const statusNode = byId("evidence-index-status");
  const content = byId("evidence-index-content");
  if (!index) {
    statusNode.dataset.state = "unavailable";
    content.dataset.state = "unavailable";
    localizedText("evidence-index-status", "UNAVAILABLE", { showRaw: false });
    [
      "evidence-index-run-id",
      "evidence-index-entry-count",
      "evidence-index-digest",
      "evidence-index-pack-digest",
      "evidence-index-classes",
      "evidence-index-verifier",
      "evidence-index-privacy",
      "evidence-index-target-writes",
    ].forEach((id) => text(id, displayToken("NOT_OBSERVED")));
    exactAuditTitle("evidence-index-run-id");
    exactAuditTitle("evidence-index-digest");
    exactAuditTitle("evidence-index-pack-digest");
    text("evidence-index-boundary", `${t("operations.evidenceIndex.unavailable")} ${t("operations.evidenceIndex.boundary")}`);
    return;
  }

  const privacy = index.privacy;
  statusNode.dataset.state = "verified";
  content.dataset.state = "verified";
  localizedText("evidence-index-status", "PASS", { showRaw: false });
  text("evidence-index-run-id", index.run_id);
  text("evidence-index-entry-count", t("operations.evidenceIndex.entryCountValue", { count: index.entry_count }));
  text("evidence-index-digest", shortDigest(index.index_digest, 28));
  text("evidence-index-pack-digest", shortDigest(index.pack_digest, 28));
  text("evidence-index-classes", index.evidence_classes.map(displayToken).join(" · "));
  text("evidence-index-verifier", `${displayToken(index.verifier_status)} · ${t("operations.evidenceIndex.verified")}`);
  text("evidence-index-privacy", t("operations.evidenceIndex.privacyValue", {
    status: displayToken(privacy.canary_status),
    canary: displayToken(privacy.canary_reason_code),
    rejection: displayToken(privacy.rejection_reason_code),
  }));
  text("evidence-index-target-writes", t("operations.evidenceIndex.targetWritesValue", { count: index.target_writes }));
  text("evidence-index-boundary", t("operations.evidenceIndex.boundary"));
  exactAuditTitle("evidence-index-run-id", index.run_id);
  exactAuditTitle("evidence-index-digest", index.index_digest);
  exactAuditTitle("evidence-index-pack-digest", index.pack_digest);
}

function renderOperations(state) {
  const health = currentHealth || {};
  const ready = currentReadiness || {};
  const eventScopes = projectedEventScopes(state);
  const chain = eventScopes.global;
  localizedText("ops-health", currentHealth === null ? "CHECKING" : health.status === "ok" ? "HEALTHY" : "UNAVAILABLE");
  text("ops-health-detail", currentHealth === null
    ? t("common.loading")
    : health.version ? `OrgRebase ${health.version} · ${displayToken(health.profile)}` : t("operations.health.unavailable"));
  if (ready.status === "not_ready") text("ops-ready", t("operations.ready.notReady"));
  else localizedText("ops-ready", currentReadiness === null ? "CHECKING" : ready.status === "ready" ? "READY" : "UNAVAILABLE");
  let readyDetail = currentReadiness === null
    ? t("common.loading")
    : ready.status === "not_ready" ? t("operations.ready.notReadyDetail")
      : ready.status !== "ready" ? t("operations.ready.unavailable")
        : ready.workspace_store ? `${ready.workspace_store === "READY" ? t("operations.workspaceStoreConnected") : displayToken(ready.workspace_store)} · ${t("operations.card.receiptBound")}` : t("operations.ready.confirmed");
  const deploymentRefs = [];
  if (ready.status === "ready") {
    if (typeof ready.deployment_maturity === "string" && ready.deployment_maturity) {
      const knownMaturity = ["REFERENCE_RUNTIME", "SINGLE_ENTERPRISE_PILOT", "AUTHENTICATED_SINGLE_TENANT"].includes(ready.deployment_maturity);
      readyDetail += ` · ${t("operations.ready.deployment", { maturity: knownMaturity
        ? t(`operations.ready.maturity.${ready.deployment_maturity}`) : displayToken(ready.deployment_maturity) })}`;
    }
    if (typeof ready.production_ready === "boolean") readyDetail += ` · ${t(`operations.ready.production.${ready.production_ready}`)}`;
    [["profile", ready.profile_digest], ["pack", ready.enterprise_pack_digest]].forEach(([kind, digest]) => {
      if (!isSha256Digest(digest)) return;
      readyDetail += ` · ${t(`operations.ready.${kind}`, { digest: shortDigest(digest, 12) })}`;
      deploymentRefs.push(`${kind}: ${digest}`);
    });
  }
  text("ops-ready-detail", readyDetail);
  exactAuditTitle("ops-ready-detail", deploymentRefs.join("\n"));
  const workspaceLoading = currentState === null && workspaceStateAvailability === "loading";
  localizedText("ops-event-chain", workspaceLoading ? "CHECKING" : chain.status || "WAITING");
  text("ops-event-detail", workspaceLoading
    ? t("common.loading")
    : eventScopes.present
    ? `${t("operations.events.scoped", {
      quote: eventCount(eventScopes.quote),
      global: eventCount(eventScopes.global),
      oac: eventCount(eventScopes.oac),
      quoteStatus: displayToken(eventScopes.quote.status || "NOT_OBSERVED"),
    })}`
    : `${t("enum.events", { count: eventCount(chain) })}`);
  exactAuditTitle("ops-event-detail");
  const persistenceStatus = workspaceStateAvailability === "unavailable"
    || (currentReadiness !== null && ready.status !== "ready") ? "UNAVAILABLE"
    : workspaceLoading || currentReadiness === null ? "CHECKING"
      : currentState !== null && workspaceStateAvailability === "ready" ? "READY" : "UNAVAILABLE";
  localizedText("ops-persistence", persistenceStatus);
  text("ops-persistence-detail", t(`operations.persistence.${persistenceStatus.toLowerCase()}`));

  const evidence = currentRetainedEvidence || {};
  const retainedProofSpine = evidence.status === "PASS" && Array.isArray(evidence.evidence_spine)
    ? evidence.evidence_spine
    : [];
  const proofSuccess = {
    SOURCE: "SUCCEEDED",
    AGENTTEAMS: "completed",
    TOOL: "SUCCEEDED",
    SKILL: "SUCCESS",
    OTLP: "3_SIGNALS_EXPORTED_AND_QUERIED",
    CANDIDATE: "CANDIDATE_ACCEPTED",
    GOVERNED_APPLY: "GOVERNED_APPLIED",
  };
  const retainedProofVerified = retainedProofSpine.length === 7 && retainedProofSpine.every(
    (item) => proofSuccess[item.stage] === item.status && item.receipt_digest,
  );
  const execution = stateExecution(state);
  const rows = state.execution_activity && Array.isArray(state.execution_activity.rows)
    ? state.execution_activity.rows.filter((row) => sameRunActivityRow(row, execution.run_id))
    : [];
  const competition = activeCompetition(state);
  const collaboration = competition && competition.agent_collaboration || {};
  const plan = collaboration.orchestration_plan || {};
  const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
  const financeAttemptTwo = tasks.find((task) => (
    task.role === "DOMAIN_WORKER"
      && task.authority_domain === "finance"
      && task.attempt === 2
  )) || null;
  const gtmComposeTask = tasks.find((task) => (
    task.role === "DOMAIN_WORKER"
      && task.authority_domain === "gtm"
      && task.attempt === 1
  )) || null;
  const businessRunStarted = state.stage !== "EMPTY";
  const firstRow = (predicate) => rows.find(predicate) || null;
  const hasReceipt = (row) => Boolean(row && row.receipt_digest);
  const sourceRow = businessRunStarted
    ? firstRow((row) => row.plane === "ADMISSION" && row.status === "PASS" && hasReceipt(row))
    : null;
  const agentRow = businessRunStarted ? nativeAgentTeamsFormationReceipt(state, rows, execution) : null;
  const toolRow = businessRunStarted
    ? exactFinanceToolInvocation(rows, execution.run_id, collaboration.tool, financeAttemptTwo)
    : null;
  const skillRow = businessRunStarted
    ? exactGtmSkillInvocation(rows, execution.run_id, collaboration.skill, gtmComposeTask)
    : null;
  const controlRow = businessRunStarted ? rows.find((row) => (
    row.plane === "FORMATION"
      && row.action === "FORM_DOMAIN_COALITION_CONTEXT_AND_QUOTE_V1"
      && row.permission === "CANONICAL_WRITE"
      && row.status === "COMPLETED"
      && hasReceipt(row)
  )) || null : null;
  const currentItems = [
    { order: 1, stage: "SOURCE", row: sourceRow, status: sourceRow && sourceRow.status },
    {
      order: 2,
      stage: "AGENTTEAMS",
      row: agentRow,
      status: agentRow && "completed",
    },
    { order: 3, stage: "TOOL", row: toolRow, status: toolRow && toolRow.status },
    { order: 4, stage: "SKILL", row: skillRow, status: skillRow && skillRow.status },
    { order: 5, stage: "CONTROL", row: controlRow, status: controlRow && controlRow.status },
  ].map((item) => ({
    order: item.order,
    stage: item.stage,
    status: item.status || "WAITING",
    target_writes: item.row && Number.isInteger(item.row.target_writes) ? item.row.target_writes : null,
    receipt_digest: item.row && item.row.receipt_digest || null,
    tone: item.status ? (item.stage === "CONTROL" ? "human" : "pass") : "not-run",
  }));
  const currentObserved = currentItems.filter((item) => item.status !== "WAITING").length;
  const currentRunComplete = currentRunValueProjection(state).status === "PASS";
  const completionKey = currentRunCompletionKey(state);
  const archiveObserved = currentRunArchiveRequestedFor === completionKey && currentRunArchive !== null;
  const archiveVerified = currentRunComplete && currentRunArchiveProof(currentRunArchive, execution.run_id);
  const observabilityObserved = currentRunObservabilityRequestedFor === completionKey
    && currentRunObservability !== null;
  const terminalProjection = archiveVerified
    ? completedRunObservabilityProjection(currentRunObservability, currentRunArchive, execution.run_id)
    : null;
  const projectionClosed = currentRunComplete && (
    (archiveObserved && !archiveVerified) || (observabilityObserved && !terminalProjection)
  );
  const displayedItems = terminalProjection
    ? terminalProjection.items.map((item) => ({
      ...item,
      detail: t("operations.sameRun.projectedNodeDetail"),
    }))
    : currentItems;
  text("ops-proof-title", currentObserved > 0 ? t("operations.sameRun.runLabel") : t("operations.sameRun.unavailable"));
  exactAuditTitle("ops-proof-title", currentObserved > 0 ? execution.run_id : null);
  text("ops-proof-status", terminalProjection
    ? t("operations.sameRun.projectionVerified")
    : currentRunComplete
      ? t(projectionClosed
        ? "operations.sameRun.projectionClosed"
        : archiveVerified
          ? "operations.sameRun.projectionLoading"
          : "operations.sameRun.projectionWaitingArchive")
      : currentObserved === currentItems.length
        ? t("operations.sameRun.verified")
        : currentObserved > 0
          ? t("operations.sameRun.partial", { observed: currentObserved, total: currentItems.length })
          : t("operations.sameRun.unavailable"));
  byId("ops-proof-spine").innerHTML = operationProofLane(
    t(terminalProjection ? "operations.sameRun.projectionLane" : "operations.sameRun.currentLane"),
    displayedItems,
  );
  text("ops-proof-boundary", t(terminalProjection
    ? "operations.sameRun.projectionBoundary"
    : projectionClosed
      ? "operations.sameRun.projectionClosedBoundary"
      : archiveVerified
        ? "operations.sameRun.boundaryComplete"
        : "operations.sameRun.boundary"));
  if (currentRetainedEvidence === null) {
    byId("ops-receipts").innerHTML = operationReceiptCard(
      t("operations.retained.title"),
      t("proofOverview.loading"),
      t("retained.ledger.loading"),
    );
    text("ops-boundary", currentRunComplete
      ? t("operations.boundary.active")
      : activeCompetition(state) && currentObserved === currentItems.length
        ? t("operations.boundary.formation")
        : t("operations.boundary.inactive"));
    return;
  }
  renderEvidenceIndexArchive(evidence);
  const operations = evidence.status === "PASS" ? (evidence.operations || {}) : {};
  const recovery = evidence.status === "PASS" ? (evidence.recovery || {}) : {};
  const alert = operations.alert || {};
  const negativeAlert = alert.negative_probe || {};
  const retention = operations.retention || {};
  const query = operations.query || {};
  const capacity = operations.capacity || {};
  const backup = operations.backup || {};
  const connectors = operations.connectors || {};
  const source = connectors.source || {};
  const tool = connectors.tool || {};
  const otlp = operations.otlp || {};
  const readiness = operations.enterprise_readiness || {};
  const cards = [
    operationReceiptCard(
      t("operations.card.alert"),
      alert.status || "UNAVAILABLE",
      negativeAlert.status === "ALERT"
        ? t("operations.card.alertsVerified", {
          healthy: alert.alerts ?? "—",
          negative: negativeAlert.alerts ?? "—",
          severity: displayToken(negativeAlert.severity || "CRITICAL"),
        })
        : t("operations.card.alerts", { count: alert.alerts ?? "—" }),
      [alert.receipt_digest, negativeAlert.receipt_digest].filter(Boolean).join(" · "),
    ),
    operationReceiptCard(t("operations.card.retention"), retention.retained_count ?? "UNAVAILABLE", t("operations.card.retentionDetail", { deleted: retention.deleted_count ?? "—" }), retention.receipt_digest),
    operationReceiptCard(t("operations.card.query"), query.count ?? "UNAVAILABLE", t("operations.card.queryDetail"), query.receipt_digest),
    operationReceiptCard(t("operations.card.capacity"), capacity.failures === 0 ? t("operations.card.localVerified") : "UNAVAILABLE", t("operations.card.capacityDetail", { successes: capacity.successes ?? "—", p95: capacity.p95_ms ?? "—" }), capacity.receipt_digest),
    operationReceiptCard(t("operations.card.backup"), backup.status === "PASS" ? t("operations.card.singleHostVerified") : "UNAVAILABLE", t("operations.card.backupDetail", { count: backup.artifact_count ?? "—", match: backup.digest_match ? t("operations.card.match") : t("operations.card.unknown") }), backup.receipt_digest),
    operationReceiptCard(t("operations.card.recovery"), recovery.sigkill_count === 2 ? t("operations.card.completedStateRecovered") : "UNAVAILABLE", t("operations.card.recoveryDetail", { count: recovery.sigkill_count ?? "—", dispositions: (recovery.dispositions || []).map(displayToken).join(" / ") }), recovery.events && recovery.events[0] && recovery.events[0].receipt_digest),
  ];
  byId("ops-receipts").innerHTML = cards.join("");
  text("ops-integrations", evidence.status === "PASS"
    ? t("operations.integrations", { source: source.http_status, tool: tool.http_status })
    : displayToken("UNAVAILABLE"));
  const otlpFields = Array.isArray(otlp.key_fields) ? otlp.key_fields : [];
  text("ops-otlp-fields", evidence.status === "PASS" ? t("operations.otlp.summary", { status: displayToken(otlp.query_status), count: otlpFields.length }) : displayToken("UNAVAILABLE"));
  byId("ops-otlp-fields").title = evidence.status === "PASS"
    ? otlpFields.map((field) => t(OTLP_AUDIT_FIELD_LABELS[field] || field)).join(" · ")
    : "";
  const readinessSla = readiness.contractual_sla == null || readiness.contractual_sla === "NOT_RUN"
    ? t("operations.readiness.enterpriseValidation")
    : displayToken(readiness.contractual_sla);
  const readinessGeoDr = !readiness.geographic_failover || readiness.geographic_failover === "NOT_RUN"
    ? t("operations.readiness.enterpriseValidation")
    : displayToken(readiness.geographic_failover);
  text("ops-enterprise-readiness", evidence.status === "PASS"
    ? t("operations.readiness.summary", {
      version: readiness.artifact_version || "—",
      deployment: displayToken(readiness.deployment_profile),
      sbom: readiness.sbom_format,
      components: readiness.sbom_component_count,
      guide: displayToken(readiness.contributing_guide),
      sla: readinessSla,
      geoDr: readinessGeoDr,
    })
    : displayToken("UNAVAILABLE"));
  renderOperationsAudit(evidence, operations, retainedProofVerified);
  text("ops-boundary", currentRunComplete
    ? t("operations.boundary.active")
    : activeCompetition(state) && currentObserved === currentItems.length
      ? t("operations.boundary.formation")
      : t("operations.boundary.inactive"));
}

function renderEvidenceCockpit(state) {
  const execution = stateExecution(state);
  const scenario = state.scenario || {};
  const profile = state.enterprise_seed_profile || {};
  const quote = state.quote || {};
  renderEnterpriseDataJourney(state);
  renderBusinessChangeBoard(state);
  text("active-run-id", execution.run_id);
  localizedText("active-stage", state.stage);
  text("active-stage-detail", quote.version ? quoteRevisionLabel(quote.version) : t("scene.waitingQuote"));
  byId("active-stage-detail").title = quote.id || "";
  text("active-pack", shortDigest(scenario.pack_digest, 24));
  text("active-profile", profile.profile_ref || t("scene.declaredProfile"));
  localizedText("active-authority", execution.canonical_authority);
  renderActiveEvidenceSpine(state);
  renderActiveActionLedger(state);
  renderActiveCollaboration(state);
  renderCurrentChangeEvidence(state);
  renderOperations(state);
}

function renderTimeline(state) {
  const events = Array.isArray(state.change_events) ? state.change_events : [];
  const timeline = byId("timeline");
  timeline.innerHTML = events.map((event, index) => `<li data-event-id="${escapeHtml(event.event_id)}" class="${event.status === "APPLIED" ? "complete" : event.event_id === state.active_event_id ? "active" : ""}"><b>${index + 1}</b><span>${escapeHtml(event.slot_id)}</span><small>${escapeHtml(event.status)}</small></li>`).join("");
  text("resume-note", state.stage === "EMPTY" ? t("journey.new") : t("journey.restored", { stage: displayToken(state.stage) }));
}

function renderCommand(state) {
  const action = actionForState(state);
  const remaining = reviewHoldRemaining(state, action);
  const reviewSeconds = Math.max(1, Math.ceil(remaining / 1000));
  const commandBar = byId("command-bar");
  const humanMethod = ["approve", "open-experience"].includes(action.method);
  const completed = state.business_complete === true;
  const mode = humanMethod ? "human" : completed ? "verified" : "system";
  commandBar.dataset.mode = mode;
  if (action.method === "open-experience") {
    text("command-kicker", t("command.experienceReview"));
  } else if (state.business_complete === true) {
    const baseline = state.quote?.version === "v1" && state.change_history?.total === 0
      && Array.isArray(state.change_events) && state.change_events.length === 0;
    text("command-kicker", t(baseline ? "command.baseline" : currentRunValueProjection(state).status === "PASS" ? "command.completed" : "command.recorded"));
  } else if ((!oacWorkspaceGateObserved || !currentOacWorkspaceGate.valid) && state.stage === "EMPTY") {
    text("command-kicker", t("command.oacChecking"));
  } else if (action.method === "open-oac") {
    text("command-kicker", t("command.oacGate"));
  } else if (action.method === "approve" && remaining > 0) {
    text("command-kicker", t("command.ownerReview", { seconds: reviewSeconds }));
  } else if (action.method === "approve") {
    text("command-kicker", t("command.ownerAction"));
  } else if (action.method === "form") {
    text("command-kicker", t("command.humanStart"));
  } else {
    text("command-kicker", t("command.autoControl"));
  }
  text("command-title", action.title);
  exactAuditTitle("command-title");
  text(
    "command-detail",
    action.method === "approve" && remaining > 0
      ? t("command.reviewWait", { seconds: reviewSeconds })
      : action.detail,
  );
  exactAuditTitle("command-detail");
  localizedBusinessText("current-actor", action.actor);
  text("actor-role", action.role);
  const approvalButtonKey = action.kind === "launch_date"
    ? "command.approveLaunchButton"
    : action.kind === "currency"
      ? "command.approveCurrencyButton"
      : "command.approveButton";
  primaryButton.textContent = action.method === "approve"
    ? remaining > 0 ? t("command.reviewButton", { seconds: reviewSeconds }) : t(approvalButtonKey)
    : action.label;
  primaryButton.hidden = completed;
  primaryButton.disabled = inFlight || !action.method || remaining > 0;
  primaryButton.dataset.method = action.method || "";
  primaryButton.dataset.kind = action.kind || "";
  primaryButton.dataset.mode = mode;
  const hasQuote = Boolean(state.quote);
  quoteDownloadButton.hidden = !completed;
  evidenceDownloadButton.hidden = !completed;
  quoteDownloadButton.classList.toggle("button-primary", completed);
  quoteDownloadButton.classList.toggle("button-secondary", !completed);
  quoteDownloadButton.disabled = inFlight || !hasQuote;
  evidenceDownloadButton.disabled = inFlight || !hasQuote;
  scheduleReviewRender(remaining);
}

function renderFormInFlightCommand() {
  const commandBar = byId("command-bar");
  commandBar.dataset.mode = "system";
  text("command-kicker", t("command.formRunning"));
  text("command-title", t("command.formRunning.title"));
  text("command-detail", t("command.formRunning.detail"));
  localizedBusinessText("current-actor", t("command.formRunning.actor"));
  text("actor-role", t("command.formRunning.role"));
  primaryButton.textContent = t("command.formRunning.button");
  primaryButton.disabled = true;
}

function admittedPricingInputs(state) {
  const source = state?.enterprise_data_lineage?.source;
  if (source?.status !== "ADMITTED_AND_MATCHED" || !Array.isArray(source.values)) return null;
  const baskets = source.values.filter(item => item?.slot_id === "quote_basket");
  const policies = source.values.filter(item => item?.slot_id === "pricing_policy");
  if (baskets.length !== 1 || policies.length !== 1) return null;
  const basket = baskets[0], policy = policies[0];
  if (!basket.value || !policy.value || !Array.isArray(basket.value.items) || !basket.value.items.length
    || typeof basket.value.currency !== "string" || !/^[A-Z]{3}$/.test(basket.value.currency)
    || !Number.isSafeInteger(policy.value.discount_bps) || policy.value.discount_bps < 0 || policy.value.discount_bps > 10000
    || !Number.isSafeInteger(policy.value.tax_bps) || policy.value.tax_bps < 0 || policy.value.tax_bps > 10000
    || policy.value.tax_mode !== "EXCLUSIVE"
    || ![basket.source_ref, policy.source_ref].every(ref => typeof ref === "string" && ref.startsWith("source:") && ref.length < 512)) return null;
  return { lineCount: basket.value.items.length, currency: basket.value.currency,
    basketSource: basket.source_ref, policySource: policy.source_ref,
    discountBps: policy.value.discount_bps, taxBps: policy.value.tax_bps };
}

function renderQuotePricing(pricing) {
  const section = byId("quote-pricing");
  if (!section) return;
  section.replaceChildren();
  section.hidden = !pricing || !Array.isArray(pricing.lines);
  if (section.hidden) return;
  const money = value => typeof value === "string" ? `${value} ${pricing.currency}` : "—";
  const rate = value => Number.isSafeInteger(value) && value >= 0
    ? `${Math.floor(value / 100)}.${String(value % 100).padStart(2, "0")}%` : "—";
  const heading = document.createElement("h3"); heading.textContent = t("quotePricing.title"); section.append(heading);
  const scroll = document.createElement("div"); scroll.className = "quote-pricing-scroll";
  const table = document.createElement("table"); table.className = "quote-pricing-lines";
  const caption = document.createElement("caption"); caption.textContent = t("quotePricing.caption"); table.append(caption);
  const head = document.createElement("thead"), headers = document.createElement("tr");
  for (const title of [t("quotePricing.item"), t("quotePricing.quantity"), t("quotePricing.unitPrice"), t("quotePricing.lineAmount")]) {
    const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = title; headers.append(cell);
  }
  head.append(headers); table.append(head);
  const body = document.createElement("tbody");
  for (const line of pricing.lines) {
    const row = document.createElement("tr"); row.dataset.lineId = line.line_id;
    const item = document.createElement("td"), description = document.createElement("span"), identity = document.createElement("small");
    description.textContent = line.description || line.sku;
    identity.className = "quote-line-source";
    identity.textContent = [line.sku && `SKU ${line.sku}`, line.line_id && t("quotePricing.lineId", { id: line.line_id })].filter(Boolean).join(" · ");
    item.append(description, identity); row.append(item);
    for (const value of [String(line.quantity), money(line.unit_price), money(line.line_total)]) {
      const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
    }
    body.append(row);
  }
  table.append(body); scroll.append(table); section.append(scroll);
  const totals = document.createElement("dl"); totals.className = "quote-pricing-totals";
  const values = [
    [t("quotePricing.subtotal"), money(pricing.subtotal)],
    [t("quotePricing.discount", { rate: rate(pricing.discount_rate_bps) }), money(pricing.discount_amount)],
    [t("quotePricing.net"), money(pricing.net_amount)],
    [`${pricing.tax_label || t("quotePricing.tax")} (${rate(pricing.tax_rate_bps)})`, money(pricing.tax_amount)],
    [t("quotePricing.total"), money(pricing.total)],
  ];
  for (const [name, value] of values) {
    const row = document.createElement("div"), term = document.createElement("dt"), amount = document.createElement("dd");
    term.textContent = name; amount.textContent = value; row.append(term, amount); totals.append(row);
  }
  section.append(totals);
  const sources = document.createElement("details"), title = document.createElement("summary"), facts = document.createElement("dl");
  title.textContent = t("quotePricing.sources");
  for (const [name, value] of [
    [t("quotePricing.basketSource"), pricing.basket_source_ref],
    [t("quotePricing.policySource"), pricing.policy_source_ref],
    [t("quotePricing.rounding"), pricing.rounding_description],
    [t("quotePricing.basketDigest"), pricing.basket_digest],
    [t("quotePricing.policyDigest"), pricing.policy_digest],
  ]) {
    const row = document.createElement("div"), term = document.createElement("dt"), valueNode = document.createElement("dd");
    term.textContent = name; valueNode.textContent = value || "—"; row.append(term, valueNode); facts.append(row);
  }
  sources.append(title, facts); section.append(sources);
  const boundary = document.createElement("p"); boundary.className = "quote-pricing-boundary";
  boundary.textContent = t("quotePricing.boundary");
  section.append(boundary);
}

function renderQuote(state) {
  const quote = state.quote;
  renderQuotePricing(quote?.payload?.pricing);
  const empty = byId("quote-empty");
  const grid = byId("quote-grid");
  const coalitionReady = Boolean(state.formation || (state.coalition && state.coalition.plan_ref));
  document.querySelectorAll("#coalition > div").forEach((node) => node.classList.toggle("active", coalitionReady));
  text("coalition-status", coalitionReady ? t("quote.coalition.ready") : displayToken("WAITING"));

  if (!quote) {
    empty.hidden = false;
    grid.hidden = true;
    text("quote-version", t("enum.notFormed"));
    byId("quote-version").removeAttribute("title");
    return;
  }

  const payload = quote.payload || {};
  empty.hidden = true;
  grid.hidden = false;
  text("quote-version", quoteRevisionLabel(quote.version));
  byId("quote-version").removeAttribute("title");
  text("quote-workflow-status", t(quoteWorkflowKey(state.stage)));
  text("quote-decision", t(quoteDecisionKey(state.stage)));
  text("quote-external-lifecycle", t("quote.external.unmanaged"));
  const customer = payload.customer_id || t("quote.customerUnknown");
  const owner = payload.owner || t("quote.ownerUnknown");
  text("quote-customer", `${displayToken(customer)} / ${displayToken(owner)}`);
  byId("quote-customer").removeAttribute("title");
  localizedBusinessText("quote-plan", payload.product_plan);
  text("quote-launch", payload.launch_date);
  localizedBusinessText("quote-residency", payload.data_residency);
  text("quote-notice", payload.notice_required === true ? t("quote.notice.required") : payload.notice_required === false ? t("quote.notice.notRequired") : "—");
  localizedBusinessText("quote-price", payload.price_band);
  localizedBusinessText("quote-currency", payload.currency);
  localizedBusinessText("quote-terms", payload.partner_terms_code);
  const versions = quoteVersionPath(state, [
    changeProjection("launch_date", state.changes && state.changes.launch_date),
    changeProjection("currency", state.changes && state.changes.currency),
  ].filter((projection) => projection.delta));
  text("quote-lineage", versions.length > 1
    ? t("quote.lineage.summary", { path: quoteRevisionPathLabel(versions) })
    : t("quote.lineage.current", { version: quoteRevisionLabel(quote.version) }));
  byId("quote-lineage").removeAttribute("title");
}

function impactTone(classification) {
  if (classification === "UNKNOWN") return "unknown";
  if (classification === "UNAFFECTED_WITHIN_DECLARED_BOUNDARY") return "bounded";
  if (classification === "REQUALIFICATION_REQUIRED") return "skill";
  return "affected";
}

function impactCaption(classification) {
  if (classification === "UNKNOWN") return t("impact.unknown");
  if (classification === "UNAFFECTED_WITHIN_DECLARED_BOUNDARY") return t("impact.preserve");
  if (classification === "REQUALIFICATION_REQUIRED") return t("impact.requalify");
  return t("impact.rebuild");
}

function proofLabel(item) {
  const path = Array.isArray(item.proof_path) ? item.proof_path : [];
  if (path.length) {
    return path.map((step) => displayToken(step.relation || step.edge_id || step.id)).filter(Boolean).join(" → ");
  }
  return displayToken(item.reason_code || item.reason || t("impact.noProofPath"));
}

function rawProofLabel(item) {
  const path = Array.isArray(item.proof_path) ? item.proof_path : [];
  if (path.length) {
    return path.map((step) => step.edge_id || step.id || step.relation).filter(Boolean).join(" → ");
  }
  return item.reason_code || item.reason || t("impact.noProofPath");
}

function classificationCounts(preview) {
  if (preview.counts) return preview.counts;
  const counts = {
    affected_hard: 0,
    bounded_unaffected: 0,
    unknown: 0,
    skill_requalification: 0,
  };
  (preview.results || []).forEach((item) => {
    if (item.classification === "AFFECTED_HARD") counts.affected_hard += 1;
    if (item.classification === "UNAFFECTED_WITHIN_DECLARED_BOUNDARY") counts.bounded_unaffected += 1;
    if (item.classification === "UNKNOWN") counts.unknown += 1;
    if (item.classification === "REQUALIFICATION_REQUIRED") counts.skill_requalification += 1;
  });
  return counts;
}

function renderImpact(state) {
  const latest = activePreview(state);
  const bundle = previewBundle(state);
  const preview = bundle && (bundle.preview || bundle.impact_preview);
  const observedCertificate = bundle && (bundle.minimal_rebase_certificate || bundle.certificate);
  const exactCertificate = exactVmrcBinding(bundle);
  const list = byId("impact-list");
  const certificateNode = byId("certificate-digest");

  if (!preview) {
    localizedText("preview-status", "NOT_RUN");
    ["affected-count", "bounded-count", "unknown-count", "skill-count"].forEach((id) => text(id, "—"));
    text("preview-digest", "—");
    text("certificate-digest", "—");
    certificateNode.dataset.state = "unobserved";
    exactAuditTitle("preview-digest");
    exactAuditTitle("certificate-digest");
    list.innerHTML = `<p class="empty-copy">${escapeHtml(t("impact.empty"))}</p>`;
    return;
  }

  const counts = classificationCounts(preview);
  byId("preview-status").innerHTML = `${escapeHtml(displayToken((latest && latest.kind) || "CHANGE"))} · ${localizedFactHtml("LOCKED", { showRaw: false })}`;
  text("affected-count", counts.affected_hard ?? "—");
  text("bounded-count", counts.bounded_unaffected ?? "—");
  text("unknown-count", counts.unknown ?? "—");
  text("skill-count", counts.skill_requalification ?? "—");
  const previewDigest = (latest && latest.preview_digest) || preview.digest;
  const previewBound = isSha256Digest(preview.digest)
    && (!latest || !latest.preview_digest || latest.preview_digest === preview.digest);
  text("preview-digest", previewBound && previewDigest ? t("impact.previewEvidenceBound") : displayToken("NOT_OBSERVED"));
  if (exactCertificate) {
    certificateNode.dataset.state = "verified";
    text("certificate-digest", t("impact.certificateVerified"));
  } else if (observedCertificate) {
    certificateNode.dataset.state = "invalid";
    text("certificate-digest", t("impact.certificateInvalid"));
  } else {
    certificateNode.dataset.state = "unobserved";
    text("certificate-digest", displayToken("NOT_OBSERVED"));
  }
  exactAuditTitle("preview-digest");
  exactAuditTitle("certificate-digest");

  const results = Array.isArray(preview.results) ? preview.results : [];
  list.innerHTML = results.length
    ? results.map((item) => `<div class="impact-row ${impactTone(item.classification)}">
        <span class="impact-dot" aria-hidden="true"></span>
        <div><strong>${escapeHtml(displayToken(item.label || item.target_id || item.object_id || "Impact target"))}</strong><small>${escapeHtml(proofLabel(item))}</small></div>
        <em>${escapeHtml(impactCaption(item.classification))}</em>
      </div>`).join("")
    : `<p class="empty-copy">${escapeHtml(t("impact.emptyLocked"))}</p>`;
}

function approvalActor(latest) {
  if (!latest) return null;
  const approval = latest.approval || latest;
  return approval.actor_id || approval.approved_by || latest.actor_id || null;
}

function approvalRecords(state) {
  return Object.keys(state.changes || {})
    .map((kind) => state.changes && state.changes[kind] && state.changes[kind].approval)
    .filter(Boolean);
}

function renderGovernance(state) {
  const approvals = approvalRecords(state);
  const latestApproval = activeApproval(state);
  const visibleApprovals = approvals.length ? approvals : latestApproval ? [latestApproval] : [];
  const actors = visibleApprovals.map(approvalActor).filter(Boolean);
  const digests = visibleApprovals
    .map((record) => record.approval_digest || record.artifact_digest || (record.approval && record.approval.digest))
    .filter(Boolean);
  text("approval-actor", actors.map(displayToken).join(" + "));
  byId("approval-actor").removeAttribute("title");
  text("approval-digest", digests.length ? t("governance.approvalsBound", { count: digests.length }) : "—");
  exactAuditTitle("approval-digest");

  const graph = state.graph_pointer || {};
  const snapshot = state.graph_snapshot || {};
  const graphRef = graph.id && graph.version ? `${graph.id}@${graph.version}` : graph.payload && graph.payload.snapshot_ref;
  const exactGraphRef = graphRef || snapshot.ref || snapshot.id;
  text("successor-graph", exactGraphRef ? t("governance.successorReady") : "—");
  exactAuditTitle("successor-graph");
  localizedText("recovery-status", Boolean(state.quote) ? "PERSISTED · REOPENABLE" : "WAITING");

  const toolEvidence = state.dependency_evidence_tool || {};
  const toolStatus = toolEvidence.status || "NOT_RUN";
  byId("dependency-tool-status").innerHTML = toolStatus === "SUCCEEDED"
    ? `${localizedFactHtml("SUCCEEDED")} · ${localizedFactHtml("READ_ONLY")} · ${escapeHtml(t("governance.zeroWrites"))}`
    : localizedFactHtml(toolStatus);
  text("dependency-tool-receipt", toolEvidence.invocation_artifact_id ? t("governance.toolReceiptBound") : "—");
  exactAuditTitle("dependency-tool-receipt");

  const chain = projectedEventScopes(state).quote;
  const events = eventCount(chain);
  const passed = chain.status === "PASS" && events > 0;
  localizedText("event-chain-status", passed ? "PASS" : "WAITING");
  text("event-chain-detail", passed ? t("governance.events", { count: events }) : t("governance.noEvents"));
  exactAuditTitle("event-chain-detail");
  const seal = byId("integrity-seal");
  seal.textContent = passed ? t("governance.passSeal") : t("governance.waitSeal");
  seal.dataset.state = passed ? "pass" : "waiting";
}

function renderActiveRunBoundary(normalized) {
  if (currentRunValueProjection(normalized).status === "BASELINE_VERIFIED") {
    text("active-boundary-note", t("value.current.baseline.detail"));
    exactAuditTitle("active-boundary-note");
    return;
  }
  const competition = activeCompetition(normalized);
  const currentRunComplete = currentRunValueProjection(normalized).status === "PASS";
  if (competition) {
    text(
      "active-boundary-note",
      t(currentRunComplete ? "active.boundary.golden" : "active.boundary.goldenFormation", {
        runId: competition.run_id,
        correlationId: competition.correlation_id,
        stage: displayToken(normalized.stage),
      }),
    );
    exactAuditTitle("active-boundary-note");
  } else if (normalized.stage === "EMPTY") {
    text(
      "active-boundary-note",
      t("active.boundary.pending", { profile: t("active.profileAdmitted") }),
    );
    exactAuditTitle("active-boundary-note");
  } else {
    const oacActivation = normalized.execution && normalized.execution.oac_activation || {};
    const boundaryKey = oacActivation.status === "CONSUMED_BY_QUOTE_FORMATION"
      ? "active.boundary.oac"
      : currentRunComplete
        ? "active.boundary.complete"
        : "active.boundary.active";
    text(
      "active-boundary-note",
      t(boundaryKey, {
        profile: t("active.profileAdmitted"),
        stage: displayToken(normalized.stage),
      }),
    );
    exactAuditTitle("active-boundary-note");
  }
}

function renderCurrentTaskBadge(state) {
  renderActiveRunBoundary(state);
  const badge = byId("semifinal-status-badge");
  const terminal = currentRunValueProjection(state).status === "PASS";
  text("semifinal-status-badge", state.stage === "EMPTY"
    ? t("header.currentTask.waiting")
    : currentRunValueProjection(state).status === "BASELINE_VERIFIED"
      ? t("header.currentTask.baseline")
    : terminal
      ? t("header.currentTask.complete")
      : t("header.currentTask.progress", { stage: displayToken(state.stage) }));
  badge.classList.toggle("badge-pass", terminal);
}

function terminalRunId(state) {
  const execution = state && state.execution || {};
  return state && state.business_complete === true && typeof execution.run_id === "string"
    ? execution.run_id
    : null;
}

function currentRunCompletionKey(state) {
  const runId = terminalRunId(state);
  if (!runId) return null;
  const events = (state.change_events || []).map(event => [
    event.event_id, event.event_digest, event.status,
    state.changes?.[event.event_id]?.approval?.approval_digest,
    state.changes?.[event.event_id]?.outcome?.outcome?.workspace_rebase_receipt?.digest,
  ]).sort((left, right) => String(left[0]).localeCompare(String(right[0])));
  return JSON.stringify([state.workspace_id || "default", runId, state.quote?.id,
    state.quote?.version, state.quote?.digest, state.change_history?.total, events]);
}

function clearCurrentRunArchive(statusKey, state = "PENDING") {
  const root = byId("current-run-archive");
  const status = byId("current-run-archive-status");
  if (!root || !status) return;
  root.dataset.state = state;
  text("current-run-archive-title", t("currentArchive.title"));
  status.dataset.state = state.toLowerCase();
  status.textContent = t(statusKey);
  [
    "current-run-archive-id",
    "current-run-archive-quote",
    "current-run-archive-fields",
    "current-run-archive-receipts",
    "current-run-archive-authority",
  ].forEach((id) => {
    text(id, "—");
    exactAuditTitle(id);
  });
}

const verifiedApprovalAuthorities = new WeakMap();

function canonicalAuthorityDocument(value) {
  if (value === null || typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalAuthorityDocument).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalAuthorityDocument(value[key])}`).join(",")}}`;
  throw new Error("APPROVAL_AUTHORITY_VALUE_INVALID");
}

async function verifyArchiveApprovalAuthorities(view) {
  if (!["orgrebase.workspace-current-run-archive-view.v2", "orgrebase.workspace-current-run-archive-view.v3"].includes(view?.schema_version)) return;
  const digest = async value => {
    const bytes = new TextEncoder().encode(canonicalAuthorityDocument(value));
    const hashed = await crypto.subtle.digest("SHA-256", bytes);
    return "sha256:" + [...new Uint8Array(hashed)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  };
  for (const receipt of view.record?.selective_rebase_receipts || []) {
    if (receipt.kind === "source_readmission_group") {
      if (view.schema_version !== "orgrebase.workspace-current-run-archive-view.v3") throw new Error("SOURCE_GROUP_ARCHIVE_SCHEMA_INVALID");
      await window.OrgRebaseSourceReadmissionProof.verify(receipt);
      continue;
    }
    const authority = receipt.approval_authority;
    if (authority === undefined) continue;
    if (!authority || authority.schema_version !== "orgrebase.workspace-approval-authority.v1"
      || authority.evidence_class !== "VERIFIED_EVENT_SCOPED_APPROVAL_AUTHORITY"
      || await digest(authority.provenance) !== authority.provenance_digest
      || (authority.grant === null ? authority.grant_digest !== null : await digest(authority.grant) !== authority.grant_digest)) throw new Error("APPROVAL_AUTHORITY_DIGEST_INVALID");
    verifiedApprovalAuthorities.set(authority, canonicalAuthorityDocument(authority));
  }
}

function archiveApprovalAuthorityBound(receipt, runId, eventDigest = null) {
  const authority = receipt.approval_authority;
  if (authority === undefined) return receipt.approval_actor_id === undefined;
  try {
    if (!authority || verifiedApprovalAuthorities.get(authority) !== canonicalAuthorityDocument(authority)) return false;
    const provenance = authority.provenance, grant = authority.grant;
    const validIdentity = identity => identity && ["issuer", "subject", "actor_id"].every(key => typeof identity[key] === "string" && identity[key].length > 0);
    if (!validIdentity(provenance.identity) || provenance.approval_digest !== receipt.approval_digest
      || provenance.identity.actor_id !== receipt.approval_actor_id || !isSha256Digest(provenance.event_digest)
      || (eventDigest !== null && provenance.event_digest !== eventDigest)) return false;
    if (receipt.approval_actor_id === receipt.owner_id) return grant === null && authority.grant_digest === null && provenance.grant_digest === null;
    return Boolean(grant && validIdentity(grant.owner) && validIdentity(grant.delegate)
      && provenance.grant_digest === authority.grant_digest && isSha256Digest(authority.grant_digest)
      && canonicalAuthorityDocument(provenance.identity) === canonicalAuthorityDocument(grant.delegate)
      && grant.owner.actor_id === receipt.owner_id && grant.owner.issuer === grant.delegate.issuer
      && grant.event_id === receipt.kind && grant.event_digest === provenance.event_digest && grant.execution_run_id === runId
      && Array.isArray(grant.actions) && grant.actions.length === 2 && grant.actions[0] === "APPROVE" && grant.actions[1] === "REJECT"
      && Number.isFinite(Date.parse(grant.issued_at)) && Date.parse(grant.expires_at) > Date.parse(grant.issued_at));
  } catch (_) { return false; }
}

function currentRunArchiveProof(view, runId) {
  const record = view && view.record;
  const quote = record && record.quote;
  const receipts = record && record.selective_rebase_receipts;
  const receiptKinds = Array.isArray(receipts)
    ? receipts.map((receipt) => receipt && receipt.kind)
    : [];
  const legacy = view && view.schema_version === "orgrebase.workspace-current-run-archive-view.v1";
  const grouped = view && view.schema_version === "orgrebase.workspace-current-run-archive-view.v3";
  const current = view && (view.schema_version === "orgrebase.workspace-current-run-archive-view.v2" || grouped);
  const legacySequence = legacy && view.stage === "QUOTE_V3" && Array.isArray(receipts)
    && receipts.length === 2 && receiptKinds[0] === "launch_date" && receiptKinds[1] === "currency"
    && receipts[0].successor_quote_version === "v2" && receipts[1].successor_quote_version === "v3";
  const baselineSequence = current && view.business_complete === true && record?.business_complete === true
    && Array.isArray(receipts) && receipts.length === 0 && record.human_approval_count === 0
    && Array.isArray(record.change_dispositions) && record.change_dispositions.length === 0
    && typeof quote?.ref === "string" && quote.ref.endsWith("@v1");
  const currentSequence = current && view.business_complete === true && Array.isArray(receipts) && receipts.length > 0
    && new Set(receipts.map(receipt => receipt.kind === "source_readmission_group" ? `${receipt.kind}:${receipt.group_id}` : receipt.kind)).size === receipts.length
    && receipts.every((receipt, index) => receipt.successor_quote_version === `v${index + 2}`);
  return Boolean(
    view
      && (legacySequence || currentSequence || baselineSequence)
      && view.status === "ARCHIVED"
      && Array.isArray(view.failures)
      && view.failures.length === 0
      && view.claim_boundary === "CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING"
      && view.run_id === runId
      && record
      && record.archive_class === "CURRENT_BUSINESS_RUN_COMPLETION"
      && record.same_run_as_current_task === true
      && record.terminal_status === "COMPLETED"
      && record.run_id === runId
      && Number.isSafeInteger(record.human_approval_count)
      && (record.human_approval_count > 0 || baselineSequence)
      && Array.isArray(receipts)
      && receipts.reduce((count, item) => count + (grouped && item.kind === "source_readmission_group" ? item.human_approval_count : 1), 0) === record.human_approval_count
      && quote
      && typeof quote.ref === "string"
      && (baselineSequence || quote.ref.endsWith(`@${receipts[receipts.length - 1].successor_quote_version}`))
      && receipts.every((receipt) => (
        grouped && receipt.kind === "source_readmission_group"
          ? window.OrgRebaseSourceReadmissionProof.bound(receipt, runId, record.change_dispositions)
          : receipt
          && typeof receipt.kind === "string"
          && receipt.kind.length > 0
          && (!current || archiveApprovalAuthorityBound(receipt, runId))
          && typeof receipt.owner_id === "string"
          && receipt.owner_id.length > 0
          && isSha256Digest(receipt.preview_digest)
          && isSha256Digest(receipt.approval_digest)
          && isSha256Digest(receipt.rebase_receipt_digest)
          && isSha256Digest(receipt.workspace_receipt_digest)
          && typeof receipt.successor_quote_version === "string"
          && receipt.successor_quote_version.length > 0
      ))
      && isSha256Digest(quote.digest)
      && Number.isSafeInteger(record.quote_event_count)
      && record.quote_event_count > 0
      && record.canonical_authority === "ORGREBASE_CONTROL_PLANE"
      && record.evidence_class === (current ? "VERIFIED_SAME_RUN_CANONICAL_STATE" : "VERIFIED_SAME_RUN_CONTROLLED_LOCAL"),
  );
}

const verifiedCompletionProjections = new WeakMap();

async function verifyCompletedRunObservability(view, archiveView) {
  if (view?.schema_version !== "orgrebase.workspace-completed-run-observability-view.v2") return;
  const wire = window.OrgRebaseWire;
  const binding = view.completion_binding;
  const receipt = view.projection_receipt;
  const unsigned = value => Object.fromEntries(Object.entries(value).filter(([key]) => key !== "digest"));
  const originalView = wire?.canonical(view), originalArchive = wire?.canonical(archiveView);
  if (!wire || binding?.archive_wire?.scheme !== wire.scheme || !receipt
    || await wire.digest(archiveView) !== binding.archive_wire.digest
    || await wire.digest(unsigned(binding)) !== binding.digest
    || await wire.digest(view.otlp) !== receipt.otlp_digest
    || await wire.digest(unsigned(receipt)) !== receipt.digest
    || wire.canonical(view) !== originalView || wire.canonical(archiveView) !== originalArchive) throw new Error("COMPLETION_PROJECTION_DIGEST_INVALID");
  verifiedCompletionProjections.set(view, {view: originalView, archive: originalArchive});
}

function completedRunObservabilityProjection(view, archiveView, runId) {
  const layers = ["SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "APPROVAL", "APPLY", "TERMINAL"];
  const statuses = {
    SOURCE: "PASS",
    AGENTTEAMS: "COMPLETED",
    TOOL: "SUCCEEDED",
    SKILL: "CANARY",
    APPROVAL: "PASS",
    APPLY: "COMPLETED",
    TERMINAL: "COMPLETED",
  };
  const evidenceClass = "CONTROLLED_LOCAL_POST_TERMINAL_PROJECTION";
  const claimBoundary = "TRUSTED_WORKSPACE_STATE_POST_TERMINAL_PROJECTION_NOT_REALTIME_NOT_PRODUCTION_OR_SLA";
  const timingClass = "SYNTHETIC_DETERMINISTIC_PROJECTION";
  const record = archiveView && archiveView.record;
  const archiveQuote = record && record.quote;
  const archiveReceipts = record && record.selective_rebase_receipts;
  const binding = view && view.completion_binding;
  const receipt = view && view.projection_receipt;
  const otlp = view && view.otlp;
  const isRecord = (value) => Boolean(value && typeof value === "object" && !Array.isArray(value));
  const exactKeys = (value, expected) => (
    isRecord(value)
      && Object.keys(value).length === expected.length
      && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
  const decodeAttributes = (values) => {
    if (!Array.isArray(values)) return null;
    const decoded = {};
    for (const item of values) {
      if (!isRecord(item) || typeof item.key !== "string" || !isRecord(item.value)) return null;
      if (Object.prototype.hasOwnProperty.call(decoded, item.key)) return null;
      const kinds = ["stringValue", "intValue", "boolValue"].filter(
        (key) => Object.prototype.hasOwnProperty.call(item.value, key),
      );
      if (kinds.length !== 1) return null;
      decoded[item.key] = item.value[kinds[0]];
    }
    return decoded;
  };
  const controlledResource = (resource) => {
    const attributes = isRecord(resource) && decodeAttributes(resource.attributes);
    return Boolean(
      attributes
        && attributes["deployment.environment.name"] === "controlled-local"
        && attributes["service.name"] === "orgrebase"
        && typeof attributes["service.version"] === "string"
        && attributes["service.version"].trim().length > 0,
    );
  };
  try {
    if (!currentRunArchiveProof(archiveView, runId)) return null;
    if (view?.schema_version === "orgrebase.workspace-completed-run-observability-view.v2") {
      const verified = verifiedCompletionProjections.get(view);
      if (!verified || verified.view !== window.OrgRebaseWire.canonical(view)
        || verified.archive !== window.OrgRebaseWire.canonical(archiveView)) return null;
      const boundary = "CURRENT_RUN_RECEIPT_COUNTS_NOT_REALTIME_NOT_PRODUCTION_OR_SLA";
      const resources = view.otlp?.resourceMetrics;
      const metric = resources?.[0]?.scopeMetrics?.[0]?.metrics?.[0];
      const points = metric?.gauge?.dataPoints;
      if (view.status !== "PROJECTED" || view.run_id !== runId || view.failures?.length !== 0
        || view.projection_target_writes !== 0 || view.evidence_class !== "VERIFIED_RECEIPT_PROJECTION"
        || view.claim_boundary !== boundary || binding?.schema_version !== "orgrebase.current-completion-binding.v1"
        || binding.run_id !== runId || binding.final_quote_ref !== archiveQuote.ref || binding.final_quote_digest !== archiveQuote.digest
        || !isSha256Digest(binding.archive_digest) || !isSha256Digest(binding.event_head) || !isSha256Digest(binding.digest)
        || !Number.isSafeInteger(binding.events) || binding.events <= 0
        || binding.approval_count !== record.human_approval_count || binding.rebase_count !== archiveReceipts.length
        || receipt?.schema_version !== "orgrebase.current-completion-projection.v1" || receipt.completion_binding_digest !== binding.digest
        || receipt.evidence_class !== "VERIFIED_RECEIPT_PROJECTION" || receipt.claim_boundary !== boundary
        || receipt.realtime_observation_claimed !== false || receipt.production_backend_claimed !== false || receipt.production_sla_claimed !== false
        || receipt.synthetic_spans_created !== 0 || receipt.time_basis !== "RECEIPT_PROJECTION_OBSERVATION"
        || !Number.isFinite(Date.parse(receipt.observed_at)) || !isSha256Digest(receipt.digest) || !isSha256Digest(receipt.otlp_digest)
        || resources.length !== 1 || resources[0].scopeMetrics.length !== 1 || resources[0].scopeMetrics[0].scope.name !== "orgrebase.completed_run"
        || metric?.name !== "orgrebase.completed_run.receipts" || metric.unit !== "{receipt}" || !Array.isArray(points) || points.length !== 2
        || points[0].attributes?.[0]?.value?.stringValue !== "owner_approval" || points[0].asInt !== String(binding.approval_count)
        || points[1].attributes?.[0]?.value?.stringValue !== "quote_rebase" || points[1].asInt !== String(binding.rebase_count)) return null;
      return { status: "PASS", run_id: runId, receipt_digest: receipt.digest, summary: true,
        items: [{order:1,stage:"APPROVAL",status:"PASS",target_writes:0,receipt_digest:receipt.digest,tone:"human"},
          {order:2,stage:"APPLY",status:"COMPLETED",target_writes:0,receipt_digest:receipt.digest,tone:"pass"},
          {order:3,stage:"TERMINAL",status:"COMPLETED",target_writes:0,receipt_digest:receipt.digest,tone:"pass"}] };
    }
    if (!(
      view
        && view.schema_version === "orgrebase.workspace-completed-run-observability-view.v1"
        && view.status === "PROJECTED"
        && view.run_id === runId
        && Array.isArray(view.failures)
        && view.failures.length === 0
        && view.projection_target_writes === 0
        && view.evidence_class === evidenceClass
        && view.claim_ceiling === evidenceClass
        && view.claim_boundary === claimBoundary
        && isRecord(binding)
        && binding.schema_version === "orgrebase.workspace-completion-binding.v1"
        && binding.run_id === runId
        && typeof binding.organization_id === "string"
        && binding.organization_id.trim().length > 0
        && typeof binding.agentteams_project_id === "string"
        && binding.agentteams_project_id.trim().length > 0
        && binding.projection_target_writes === 0
        && binding.evidence_class === evidenceClass
        && binding.claim_boundary === claimBoundary
        && binding.source_status === "ADMITTED_AND_MATCHED"
        && binding.source_data_class === "SYNTHETIC_FIXTURE"
        && /^skill:enterprise-quote-compose@\d+\.\d+(?:\.\d+)?$/.test(binding.source_skill_ref || "")
        && binding.final_quote_ref === archiveQuote.ref
        && binding.final_quote_digest === archiveQuote.digest
        && binding.quote_event_count === record.quote_event_count
        && isSha256Digest(binding.final_quote_payload_digest)
    )) return null;
    const bindingDigests = [
      "digest",
      "lineage_digest",
      "source_pack_digest",
      "agentteams_receipt_digest",
      "agentteams_plan_digest",
      "collaboration_digest",
      "tool_receipt_digest",
      "skill_package_digest",
      "skill_receipt_digest",
      "final_quote_digest",
      "final_quote_payload_digest",
      "event_head_digest",
      "event_sequence_digest",
    ];
    if (!bindingDigests.every((key) => isSha256Digest(binding[key]))) return null;
    const approvalFields = [
      "kind",
      "owner_id",
      "preview_digest",
      "approval_digest",
      "rebase_receipt_digest",
      "workspace_receipt_digest",
      "successor_quote_version",
    ];
    if (!(
      Array.isArray(binding.approval_apply_summaries)
        && binding.approval_apply_summaries.length === 2
        && binding.approval_apply_summaries.every((summary, index) => (
          isRecord(summary)
            && approvalFields.every((key) => summary[key] === archiveReceipts[index][key])
        ))
    )) return null;
    if (!(
      isRecord(receipt)
        && receipt.schema_version === "orgrebase.workspace-observability-projection-receipt.v1"
        && receipt.status === "PASS"
        && receipt.run_id === runId
        && receipt.completion_binding_digest === binding.digest
        && isSha256Digest(receipt.otlp_digest)
        && isSha256Digest(receipt.digest)
        && receipt.timing_class === timingClass
        && receipt.projection_target_writes === 0
        && receipt.evidence_class === evidenceClass
        && receipt.claim_boundary === claimBoundary
        && receipt.realtime_observation_claimed === false
        && receipt.production_backend_claimed === false
        && receipt.production_sla_claimed === false
        && Array.isArray(receipt.layer_order)
        && receipt.layer_order.length === layers.length
        && receipt.layer_order.every((layer, index) => layer === layers[index])
        && exactKeys(receipt.layer_statuses, layers)
        && layers.every((layer) => receipt.layer_statuses[layer] === statuses[layer])
    )) return null;
    const packageMatch = /^skill-package:(enterprise-quote-compose)@(\d+\.\d+\.\d+)$/.exec(
      binding.skill_package_id || "",
    );
    const commonAttributesPass = (attributes) => Boolean(
      attributes
        && packageMatch
        && attributes["orgrebase.workflow.run_id"] === runId
        && attributes["orgrebase.organization.id"] === binding.organization_id
        && attributes["orgrebase.receipt.digest"] === binding.digest
        && attributes["orgrebase.agentteams.task.id"] === binding.agentteams_project_id
        && attributes["orgrebase.agentteams.project.id"] === binding.agentteams_project_id
        && attributes["orgrebase.skill.name"] === packageMatch[1]
        && attributes["orgrebase.skill.version"] === packageMatch[2]
        && attributes["orgrebase.skill.package.digest"] === binding.skill_package_digest
        && attributes["orgrebase.skill.invocation.receipt.digest"] === binding.skill_receipt_digest
        && attributes["orgrebase.tool.receipt.digest"] === binding.tool_receipt_digest
        && attributes["orgrebase.enterprise.lineage.digest"] === binding.lineage_digest
        && attributes["orgrebase.final.quote.digest"] === binding.final_quote_digest
        && attributes["orgrebase.evidence.class"] === evidenceClass
        && attributes["orgrebase.telemetry.timing.class"] === timingClass
        && attributes["orgrebase.target.write.count"] === "0"
    );
    if (!exactKeys(otlp, ["traces", "logs", "metrics"])) return null;
    const traceResources = otlp.traces && otlp.traces.resourceSpans;
    const traceScope = Array.isArray(traceResources)
      && traceResources.length === 1
      && controlledResource(traceResources[0].resource)
      && Array.isArray(traceResources[0].scopeSpans)
      && traceResources[0].scopeSpans.length === 1
      ? traceResources[0].scopeSpans[0]
      : null;
    const spans = traceScope && traceScope.scope && traceScope.scope.name === "orgrebase.joint"
      ? traceScope.spans
      : null;
    if (!Array.isArray(spans) || spans.length !== layers.length) return null;
    const traceId = spans[0] && spans[0].traceId;
    if (typeof traceId !== "string" || !/^[0-9a-f]{32}$/.test(traceId)) return null;
    const spanIds = [];
    for (let index = 0; index < layers.length; index += 1) {
      const span = spans[index];
      const attributes = isRecord(span) && decodeAttributes(span.attributes);
      if (!(
        attributes
          && span.traceId === traceId
          && typeof span.spanId === "string"
          && /^[0-9a-f]{16}$/.test(span.spanId)
          && !spanIds.includes(span.spanId)
          && span.name === `orgrebase.chain/${layers[index].toLowerCase()}`
          && span.kind === 1
          && span.status && span.status.code === 1
          && (index === 0
            ? !Object.prototype.hasOwnProperty.call(span, "parentSpanId")
            : span.parentSpanId === spanIds[index - 1])
          && commonAttributesPass(attributes)
          && attributes["orgrebase.chain.layer"] === layers[index]
          && attributes["orgrebase.chain.sequence"] === String(index + 1)
          && attributes["orgrebase.chain.status"] === statuses[layers[index]]
      )) return null;
      spanIds.push(span.spanId);
    }
    const logResources = otlp.logs && otlp.logs.resourceLogs;
    const logScope = Array.isArray(logResources)
      && logResources.length === 1
      && controlledResource(logResources[0].resource)
      && Array.isArray(logResources[0].scopeLogs)
      && logResources[0].scopeLogs.length === 1
      ? logResources[0].scopeLogs[0]
      : null;
    const logs = logScope && logScope.scope && logScope.scope.name === "orgrebase.joint"
      ? logScope.logRecords
      : null;
    if (!Array.isArray(logs) || logs.length !== layers.length) return null;
    if (!logs.every((entry, index) => {
      const attributes = isRecord(entry) && decodeAttributes(entry.attributes);
      return Boolean(
        attributes
          && entry.traceId === traceId
          && entry.spanId === spanIds[index]
          && entry.severityNumber === 9
          && entry.body && entry.body.stringValue === `${layers[index]}:${statuses[layers[index]]}`
          && commonAttributesPass(attributes)
          && attributes["orgrebase.chain.layer"] === layers[index]
          && attributes["orgrebase.chain.sequence"] === String(index + 1)
          && attributes["orgrebase.chain.status"] === statuses[layers[index]],
      );
    })) return null;
    const metricResources = otlp.metrics && otlp.metrics.resourceMetrics;
    const metricScope = Array.isArray(metricResources)
      && metricResources.length === 1
      && controlledResource(metricResources[0].resource)
      && Array.isArray(metricResources[0].scopeMetrics)
      && metricResources[0].scopeMetrics.length === 1
      ? metricResources[0].scopeMetrics[0]
      : null;
    const metrics = metricScope && metricScope.scope && metricScope.scope.name === "orgrebase.joint"
      ? metricScope.metrics
      : null;
    const points = Array.isArray(metrics)
      && metrics.length === 1
      && metrics[0].name === "orgrebase.chain.layer.count"
      && metrics[0].unit === "{layer}"
      && metrics[0].gauge
      ? metrics[0].gauge.dataPoints
      : null;
    if (!(
      Array.isArray(points)
        && points.length === 1
        && points[0].asInt === String(layers.length)
        && commonAttributesPass(decodeAttributes(points[0].attributes))
    )) return null;
    return {
      status: "PASS",
      run_id: runId,
      receipt_digest: receipt.digest,
      items: layers.map((stage, index) => ({
        order: index + 1,
        stage,
        status: statuses[stage],
        target_writes: 0,
        receipt_digest: receipt.digest,
        tone: ["APPROVAL", "APPLY"].includes(stage) ? "human" : "pass",
      })),
    };
  } catch (_) {
    return null;
  }
}

function renderCurrentRunArchive(view, state = currentState) {
  const runId = terminalRunId(state);
  if (!runId) {
    clearCurrentRunArchive("currentArchive.pending");
    return;
  }
  if (!view) {
    clearCurrentRunArchive("currentArchive.loading", "LOADING");
    text("current-run-archive-id", runId);
    exactAuditTitle("current-run-archive-id", runId);
    return;
  }
  const record = view && view.record;
  const quote = record && record.quote;
  const receipts = record && record.selective_rebase_receipts;
  const valid = currentRunArchiveProof(view, runId);
  if (!valid) {
    clearCurrentRunArchive(
      view && view.status === "UNAVAILABLE"
        ? "currentArchive.unavailable"
        : "currentArchive.invalid",
      view && view.status === "UNAVAILABLE" ? "UNAVAILABLE" : "INVALID",
    );
    return;
  }
  const root = byId("current-run-archive");
  const status = byId("current-run-archive-status");
  root.dataset.state = "ARCHIVED";
  text("current-run-archive-title", t(receipts.length === 0 ? "currentArchive.titleBaseline" : "currentArchive.titleReady"));
  root.dataset.runId = runId;
  status.dataset.state = "archived";
  status.textContent = t(receipts.length === 0 ? "currentArchive.baselineArchived" : "currentArchive.archived");
  text("current-run-archive-id", runId);
  exactAuditTitle("current-run-archive-id", runId);
  text("current-run-archive-quote", quote.ref);
  exactAuditTitle("current-run-archive-quote", quote.ref);
  text("current-run-archive-fields", t("currentArchive.fields", {
    launch: displayToken(quote.launch_date),
    currency: displayToken(quote.currency),
  }));
  text("current-run-archive-receipts", t("currentArchive.receiptsValue", {
    approvals: record.human_approval_count,
    rebases: receipts.length,
  }));
  text("current-run-archive-authority", displayToken(record.canonical_authority));
  exactAuditTitle("current-run-archive-authority", record.canonical_authority);
}

function refreshCompletedRunObservabilityProjection(state = currentState, archiveView = currentRunArchive) {
  const runId = terminalRunId(state);
  const completionKey = currentRunCompletionKey(state);
  const sessionRevision = workspaceSessionRevision;
  const isCurrentProjection = () => workspaceSessionRevision === sessionRevision
    && currentRunCompletionKey(currentState) === completionKey;
  if (!runId || !currentRunArchiveProof(archiveView, runId)) return;
  if (currentRunObservabilityRequestedFor === completionKey) return;
  currentRunObservability = null;
  currentRunObservabilityRequestedFor = completionKey;
  renderCurrentRunEvidenceRetry(state);
  renderOperations(state);
  api("/api/workspace/run-observability")
    .then(async (view) => {
      if (!isCurrentProjection()) return;
      if (!currentRunArchiveProof(currentRunArchive, runId)) return;
      await verifyCompletedRunObservability(view, currentRunArchive);
      if (!isCurrentProjection()) return;
      currentRunObservability = view && typeof view === "object"
        ? view
        : { status: "UNAVAILABLE", run_id: runId };
      renderCurrentRunEvidenceRetry(currentState);
      renderOperations(currentState);
      renderCurrentRunValue(currentState);
      renderAcceptanceStory(currentState);
      renderCurrentTaskBadge(currentState);
      renderCommand(currentState);
      window.dispatchEvent(new CustomEvent("orgrebase:staterendered", {detail: currentState}));
    })
    .catch(() => {
      if (!isCurrentProjection()) return;
      if (!currentRunArchiveProof(currentRunArchive, runId)) return;
      currentRunObservability = { status: "UNAVAILABLE", run_id: runId };
      renderCurrentRunEvidenceRetry(currentState);
      renderOperations(currentState);
      renderCurrentRunValue(currentState);
      renderAcceptanceStory(currentState);
      renderCurrentTaskBadge(currentState);
      renderCommand(currentState);
      window.dispatchEvent(new CustomEvent("orgrebase:staterendered", {detail: currentState}));
    });
}

function refreshCurrentRunArchive(state = currentState) {
  const runId = terminalRunId(state);
  const completionKey = currentRunCompletionKey(state);
  const sessionRevision = workspaceSessionRevision;
  const isCurrentProjection = () => workspaceSessionRevision === sessionRevision
    && currentRunCompletionKey(currentState) === completionKey;
  renderCurrentRunEvidenceRetry(state);
  if (!runId) {
    currentRunArchive = null;
    currentRunArchiveRequestedFor = null;
    currentRunObservability = null;
    currentRunObservabilityRequestedFor = null;
    renderCurrentRunArchive(null, state);
    return;
  }
  if (currentRunArchiveRequestedFor === completionKey) {
    renderCurrentRunArchive(currentRunArchive, state);
    refreshCompletedRunObservabilityProjection(state, currentRunArchive);
    return;
  }
  currentRunArchive = null;
  currentRunObservability = null;
  currentRunObservabilityRequestedFor = null;
  renderCurrentRunArchive(null, state);
  currentRunArchiveRequestedFor = completionKey;
  renderCurrentRunEvidenceRetry(state);
  api("/api/workspace/run-archive")
    .then(async (view) => {
      if (!isCurrentProjection()) return;
      await verifyArchiveApprovalAuthorities(view);
      if (!isCurrentProjection()) return;
      currentRunArchive = view;
      renderCurrentRunEvidenceRetry(currentState);
      renderCurrentRunArchive(view, currentState);
      renderCurrentRunValue(currentState);
      renderAcceptanceStory(currentState);
      renderCurrentTaskBadge(currentState);
      renderCommand(currentState);
      window.dispatchEvent(new CustomEvent("orgrebase:staterendered", {detail: currentState}));
      if (currentRunArchiveProof(view, runId)) {
        refreshCompletedRunObservabilityProjection(currentState, view);
      } else {
        renderOperations(currentState);
      }
    })
    .catch(() => {
      if (!isCurrentProjection()) return;
      currentRunArchive = { status: "UNAVAILABLE", run_id: runId };
      renderCurrentRunEvidenceRetry(currentState);
      renderCurrentRunArchive(currentRunArchive, currentState);
      renderCurrentRunValue(currentState);
      renderAcceptanceStory(currentState);
      renderCurrentTaskBadge(currentState);
      renderCommand(currentState);
      window.dispatchEvent(new CustomEvent("orgrebase:staterendered", {detail: currentState}));
      renderOperations(currentState);
    });
}

function failedCurrentRunEvidence(state = currentState) {
  const key = currentRunCompletionKey(state);
  return {
    archive: Boolean(key && currentRunArchiveRequestedFor === key && currentRunArchive?.status === "UNAVAILABLE"),
    observability: Boolean(key && currentRunObservabilityRequestedFor === key && currentRunObservability?.status === "UNAVAILABLE"),
  };
}

function renderCurrentRunEvidenceRetry(state = currentState) {
  const panel = byId("current-run-evidence-retry-panel");
  const button = byId("current-run-evidence-retry");
  if (!panel || !button) return;
  const failed = failedCurrentRunEvidence(state);
  panel.hidden = !failed.archive && !failed.observability;
  button.disabled = panel.hidden;
}

function retryCurrentRunEvidence() {
  const failed = failedCurrentRunEvidence();
  if (!failed.archive && !failed.observability) return;
  if (failed.archive) {
    currentRunArchive = null;
    currentRunArchiveRequestedFor = null;
  }
  if (failed.observability) {
    currentRunObservability = null;
    currentRunObservabilityRequestedFor = null;
  }
  refreshCurrentRunArchive();
}

function renderWorkspaceUnavailable({ notify = true, reason = null } = {}) {
  currentState = null;
  renderCurrentRunEvidenceRetry();
  workspaceStateAvailability = "unavailable";
  inFlight = false;
  clearTimeout(reviewTimer);
  reviewTimer = null;
  text("resume-note", t("journey.unavailable"));
  document.querySelectorAll("#timeline li").forEach((node) => {
    node.classList.remove("complete", "active");
    node.setAttribute("aria-current", "false");
  });
  const commandBar = byId("command-bar");
  commandBar.dataset.mode = "system";
  text("command-kicker", t("command.next"));
  text("command-title", t("command.unavailableTitle"));
  text("command-detail", t("command.unavailableDetail"));
  localizedBusinessText("current-actor", t("command.unavailableActor"));
  text("actor-role", t("command.unavailableRole"));
  primaryButton.textContent = t("command.unavailableButton");
  primaryButton.disabled = true;
  primaryButton.dataset.method = "";
  primaryButton.dataset.kind = "";
  quoteDownloadButton.disabled = true;
  evidenceDownloadButton.disabled = true;
  text("semifinal-status-badge", t("header.currentTask.unavailable"));
  byId("semifinal-status-badge").classList.remove("badge-pass");
  text("profile-data-badge", t("header.dataUnavailable"));
  text("active-boundary-note", t("active.boundary.unavailable"));
  exactAuditTitle("active-boundary-note");
  currentRunArchive = null;
  currentRunArchiveRequestedFor = null;
  currentRunObservability = null;
  currentRunObservabilityRequestedFor = null;
  clearCurrentRunArchive("currentArchive.unavailable", "UNAVAILABLE");
  renderAuthorityLadder(UNAVAILABLE_WORKSPACE_PROJECTION);
  renderCapabilityCenter(UNAVAILABLE_WORKSPACE_PROJECTION);
  byId("workspace").setAttribute("aria-busy", "false");
  renderOperations(UNAVAILABLE_WORKSPACE_PROJECTION);
  if (notify) {
    window.dispatchEvent(new CustomEvent("orgrebase:stateunavailable", {
      detail: { status: "UNAVAILABLE", ...(reason ? { reason } : {}) },
    }));
  }
}

function render(state) {
  if (!state || !STAGES.includes(state.stage)) {
    renderWorkspaceUnavailable();
    return;
  }
  const normalized = state;
  currentState = normalized;
  const deploymentGate = normalized.workspace_gate;
  const runId = normalized.execution && normalized.execution.run_id;
  const exactGate = deploymentGate && deploymentGate.schema_version === "orgrebase.workspace-oac-gate.v1"
    && runId && deploymentGate.execution_run_id === runId;
  currentOacWorkspaceGate = normalizeOacWorkspaceGate(exactGate ? deploymentGate : null);
  oacWorkspaceGateObserved = true;
  workspaceStateAvailability = "ready";
  const scenario = normalized.scenario || {};
  const profile = normalized.enterprise_seed_profile || {};
  const quote = normalized.quote || {};
  text("scenario-eyebrow", scenario.organization_id || scenario.customer_id
    ? `${displayToken(scenario.organization_id || t("hero.scenario"))} / ${displayToken(scenario.customer_id || t("quote.defaultLabel"))}`
    : t("hero.scenario"));
  localizedBusinessText("quote-heading", quote.label || scenario.label || t("quote.defaultLabel"));
  const dataClass = profile.data_class || normalized.boundaries && normalized.boundaries.data_profile;
  if (scenario.synthetic === true || dataClass === "SYNTHETIC_FIXTURE") {
    const pricing = quote.payload?.pricing;
    const hasPricing = admittedPricingInputs(normalized) || pricing && Array.isArray(pricing.lines) && pricing.lines.length > 0
      && typeof pricing.currency === "string" && typeof pricing.total === "string";
    text("profile-data-badge", t(hasPricing ? "header.syntheticPricedData" : "header.syntheticData"));
    byId("profile-data-badge").removeAttribute("title");
  } else if (dataClass) {
    localizedText("profile-data-badge", dataClass, { showRaw: false });
    byId("profile-data-badge").removeAttribute("title");
  } else {
    text("profile-data-badge", t("header.declaredData"));
    byId("profile-data-badge").removeAttribute("title");
  }
  renderTimeline(normalized);
  renderCommand(normalized);
  renderQuote(normalized);
  renderImpact(normalized);
  renderGovernance(normalized);
  renderCurrentTaskBadge(normalized);
  refreshCurrentRunArchive(normalized);
  renderEvidenceCockpit(normalized);
  renderValueAndResponsibility(
    currentOperatingModel || { status: "UNAVAILABLE" },
    normalized,
  );
  renderExperience(normalized);
  renderCapabilityCenter(normalized);
  byId("workspace").setAttribute("aria-busy", inFlight ? "true" : "false");
  window.dispatchEvent(new CustomEvent("orgrebase:staterendered", {
    detail: normalized,
  }));
}

function renderSemifinalEvidence(evidence) {
  renderRetainedEvidence(evidence || { status: "UNAVAILABLE" });
}

let workspaceStateRevision = 0;
let workspaceSessionRevision = 0;
let workspaceStateRequest = null;
let workspaceSessionIdentity = sessionIdentity(window.OrgRebaseClient.session());

function sessionIdentity(session) {
  return session ? JSON.stringify([session.mode, session.authenticated,
    session.principal?.tenant_id, session.principal?.actor_id,
    session.principal?.issuer, session.principal?.subject]) : null;
}

async function refreshState() {
  ++workspaceStateRevision;
  if (workspaceStateRequest) return workspaceStateRequest;
  const sessionRevision = workspaceSessionRevision;
  const request = (async () => {
    // Coalesce synchronous notifications, then reread if a command finishes during I/O.
    await Promise.resolve();
    while (sessionRevision === workspaceSessionRevision) {
      const revision = workspaceStateRevision;
      const workspaceId = window.OrgRebaseClient.workspace();
      try {
        const state = await api("/api/workspace/state");
        if (sessionRevision !== workspaceSessionRevision
          || workspaceId && workspaceId !== window.OrgRebaseClient.workspace()) return null;
        if (revision !== workspaceStateRevision) continue;
        render(state);
        return state;
      } catch (error) {
        if (sessionRevision !== workspaceSessionRevision) return null;
        if (revision !== workspaceStateRevision) continue;
        throw error;
      }
    }
    return null;
  })();
  workspaceStateRequest = request;
  try { return await request; }
  finally { if (workspaceStateRequest === request) workspaceStateRequest = null; }
}

function invalidateWorkspaceState({ reason = null } = {}) {
  ++workspaceSessionRevision;
  workspaceStateRequest = null;
  invalidateSkillSource();
  renderWorkspaceUnavailable({ reason });
}

function refreshChangedWorkspace() {
  refreshState().catch(() => renderWorkspaceUnavailable());
}

window.addEventListener("orgrebase:sessionended", invalidateWorkspaceState);
window.addEventListener("pagehide", invalidateWorkspaceState);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    refreshChangedWorkspace();
    loadSkillSourceCatalog();
  }
});
window.addEventListener("orgrebase:sessionchange", (event) => {
  const next = sessionIdentity(event.detail);
  const changed = workspaceSessionIdentity !== next;
  const available = event.detail && (!event.detail.authentication_required || event.detail.authenticated === true);
  if (workspaceSessionIdentity !== null && changed) {
    invalidateWorkspaceState({ reason: available ? "identity-changed" : "session-unavailable" });
  }
  // Keep the last usable identity through a temporary expiry, so its own draft
  // survives reauthentication but cannot be inherited by a different subject.
  if (available) workspaceSessionIdentity = next;
  if (available && (changed || !currentSkillCatalog || currentSkillCatalog.status === "UNAVAILABLE")) {
    loadSkillSourceCatalog();
  }
});
window.addEventListener("orgrebase:workspacechange", refreshChangedWorkspace);
window.addEventListener("orgrebase:changeselection", () => {
  if (currentState) renderAuthorityLadder(currentState);
});
window.addEventListener("orgrebase:oacadaptationchange", (event) => {
  currentOacAdaptationProof = event && event.detail || {};
  renderCurrentProofOverview();
  refreshChangedWorkspace();
});

function openOacWorkspaceGate() {
  const panel = byId("oac-adaptation");
  if (!panel) return;
  if (window.OrgRebaseWorkspaceShell && typeof window.OrgRebaseWorkspaceShell.navigate === "function") {
    window.OrgRebaseWorkspaceShell.navigate("oac");
  }
  panel.open = true;
  const summary = panel.querySelector("summary");
  if (summary && typeof summary.focus === "function") summary.focus({ preventScroll: true });
  if (typeof panel.scrollIntoView === "function") {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  toast(t("toast.oacGateOpened"));
}

function activateCockpitView(selected) {
  document.querySelectorAll(".cockpit-tab").forEach((candidate) => {
    const active = candidate.dataset.view === selected;
    candidate.classList.toggle("active", active);
    candidate.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".cockpit-view").forEach((view) => {
    const active = view.id === `cockpit-${selected}`;
    view.classList.toggle("active", active);
    view.hidden = !active;
  });
  if (selected === "operations") refreshOperationsHealth();
}

function openExperienceGovernance() {
  const panel = byId("experience-governance");
  if (!panel || panel.hidden) return;
  if (window.OrgRebaseWorkspaceShell && typeof window.OrgRebaseWorkspaceShell.navigate === "function") {
    window.OrgRebaseWorkspaceShell.navigate("skills");
  }
  activateCockpitView("skills");
  panel.open = true;
  const summary = panel.querySelector("summary");
  if (summary && typeof summary.focus === "function") summary.focus({ preventScroll: true });
  if (typeof panel.scrollIntoView === "function") {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function openTaskIntake() {
  if (
    window.OrgRebaseWorkspaceShell
    && typeof window.OrgRebaseWorkspaceShell.focusTaskIntake === "function"
  ) {
    window.OrgRebaseWorkspaceShell.focusTaskIntake();
  }
}

async function runPrimaryAction() {
  if (inFlight || !currentState) return;
  const action = actionForState(currentState);
  if (!action || !action.method) return;
  if (action.method === "open-oac") openOacWorkspaceGate();
  else if (action.method === "open-experience") openExperienceGovernance();
  else if (action.method === "open-task-intake") openTaskIntake();
  else if (action.method === "open-change" && action.kind) {
    await window.OrgRebaseChangeWorkbench.select(action.kind);
  }
}

async function downloadJson(path, fallbackName) {
  if (inFlight) return;
  try {
    const response = await window.OrgRebaseClient.fetch(path);
    if (!response.ok) {
      let payload = null;
      try { payload = await window.OrgRebaseClient.readBody(response); }
      catch (error) { if (error.code === "WORKSPACE_REQUEST_CONTEXT_CHANGED") throw error; }
      throw new Error(errorMessage(payload, response));
    }
    const blob = await window.OrgRebaseClient.readBody(response, "blob");
    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = match ? match[1] : fallbackName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(anchor.href);
    toast(t("toast.downloaded", { name: fallbackName }), "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

document.querySelectorAll(".language-option").forEach((button) => {
  button.addEventListener("click", () => setLanguage(button.dataset.language));
});
setLanguage(storedLanguage(), { persist: false });


primaryButton.addEventListener("click", runPrimaryAction);
byId("current-run-evidence-retry").addEventListener("click", retryCurrentRunEvidence);
quoteDownloadButton.addEventListener("click", () => downloadJson("/api/workspace/export/quote", "orgrebase-quote.json"));
evidenceDownloadButton.addEventListener("click", () => downloadJson("/api/workspace/export/evidence", "orgrebase-evidence-pack.json"));
const acceptanceQuoteDownloadButton = byId("acceptance-download-quote");
const acceptanceEvidenceDownloadButton = byId("acceptance-download-evidence");
if (acceptanceQuoteDownloadButton) {
  acceptanceQuoteDownloadButton.addEventListener("click", () => downloadJson(
    "/api/workspace/export/quote",
    "orgrebase-quote.json",
  ));
}
if (acceptanceEvidenceDownloadButton) {
  acceptanceEvidenceDownloadButton.addEventListener("click", () => downloadJson(
    "/api/workspace/export/evidence",
    "orgrebase-evidence-pack.json",
  ));
}
const agentWorkExpandButton = byId("agent-work-expand");
const agentWorkCollapseButton = byId("agent-work-collapse");
if (agentWorkExpandButton) {
  agentWorkExpandButton.addEventListener("click", () => {
    document.querySelectorAll("#agent-work-list > details").forEach((detail) => {
      detail.open = true;
    });
  });
}
if (agentWorkCollapseButton) {
  agentWorkCollapseButton.addEventListener("click", () => {
    document.querySelectorAll("#agent-work-list > details").forEach((detail) => {
      detail.open = false;
    });
  });
}
byId("experience-approve").addEventListener("click", () => decideExperience("APPROVE"));
byId("experience-reject").addEventListener("click", () => decideExperience("REJECT"));
byId("skill-source-catalog").addEventListener("click", (event) => {
  const button = event.target.closest("[data-skill-source-open]");
  if (button) openSkillSourceDialog(button.dataset.skillSourceOpen);
});
byId("skill-source-close").addEventListener("click", () => byId("skill-source-dialog").close());
byId("skill-source-cancel").addEventListener("click", () => {
  const skillPackage = selectedSkillSource();
  if (skillPackage) resetSkillSourceDialogToPublished(skillPackage);
});
byId("skill-source-revise").addEventListener("click", beginSkillRevision);
byId("skill-source-save").addEventListener("click", saveSkillRevisionDraft);
byId("skill-source-proposed-version").addEventListener("input", syncSkillDraftVersion);
byId("skill-source-dialog").addEventListener("close", () => {
  selectedSkillName = null;
  skillDraftEditing = false;
  skillDialogStatus = { key: "skill.source.readonlyStatus", params: {}, tone: "neutral" };
});

const cockpitTabs = Array.from(document.querySelectorAll(".cockpit-tab"));
cockpitTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => {
    activateCockpitView(tab.dataset.view);
  });
  tab.addEventListener("keydown", (event) => {
    let target = null;
    if (event.key === "ArrowRight") target = cockpitTabs[(index + 1) % cockpitTabs.length];
    if (event.key === "ArrowLeft") target = cockpitTabs[(index - 1 + cockpitTabs.length) % cockpitTabs.length];
    if (event.key === "Home") target = cockpitTabs[0];
    if (event.key === "End") target = cockpitTabs[cockpitTabs.length - 1];
    if (!target) return;
    event.preventDefault();
    target.focus();
    target.click();
  });
});

refreshState().catch((error) => {
  renderWorkspaceUnavailable();
  if (error.code !== "AUTH_SESSION_REQUIRED") toast(error.message, "error");
});

loadSkillSourceCatalog();

let supportingViewsRevision = 0;

function clearSupportingViews() {
  ++supportingViewsRevision;
  currentReleaseFacts = {};
  currentOperatingModel = { status: "UNAVAILABLE" };
  renderSemifinalEvidence({ status: "UNAVAILABLE" });
  renderPublicRealProcessValidation({ status: "UNAVAILABLE" });
  renderValueAndResponsibility(currentOperatingModel, currentState || UNAVAILABLE_WORKSPACE_PROJECTION);
  renderCurrentProofOverview();
}

async function refreshSupportingViews() {
  const revision = ++supportingViewsRevision;
  const session = window.OrgRebaseClient.session();
  if (!session || session.authentication_required && !session.authenticated) return;
  const projections = [
    ["/api/release-facts", facts => { currentReleaseFacts = facts; renderCurrentProofOverview(); }],
    ["/api/platform/evidence", renderSemifinalEvidence],
    ["/api/workspace/operating-model", model => {
      currentOperatingModel = model;
      renderValueAndResponsibility(model, currentState || UNAVAILABLE_WORKSPACE_PROJECTION);
    }],
    ["/api/public-real-process/validation", renderPublicRealProcessValidation],
  ];
  await Promise.all(projections.map(async ([endpoint, renderProjection]) => {
    try {
      const value = await api(endpoint);
      if (revision === supportingViewsRevision) renderProjection(value);
    } catch (_) {
      if (revision === supportingViewsRevision) renderProjection({ status: "UNAVAILABLE" });
    }
  }));
}

window.addEventListener("orgrebase:sessionended", clearSupportingViews);
window.addEventListener("orgrebase:sessionchange", () => { clearSupportingViews(); refreshSupportingViews(); });
window.addEventListener("pageshow", event => { if (event.persisted) refreshSupportingViews(); });
refreshSupportingViews();

function readinessProbeResult(result) {
  if (result.status === "fulfilled" && ["ready", "not_ready"].includes(result.value?.status)) return result.value;
  if (result.status === "rejected" && result.reason?.httpStatus === 503
    && result.reason.payload && typeof result.reason.payload === "object"
    && !Array.isArray(result.reason.payload)) return { status: "not_ready" };
  return { status: "unavailable" };
}

const OPERATIONS_HEALTH_REFRESH_MS = 30_000;
const OPERATIONS_HEALTH_TIMEOUT_MS = 5_000;
let operationsHealthRequest = null;
let operationsHealthTimer = null;
let operationsHealthObservedAt = null;
let operationsHealthGeneration = 0;
let operationsHealthPageActive = true;

function refreshOperationsHealth() {
  if (!operationsHealthPageActive || document.hidden) return Promise.resolve();
  if (operationsHealthRequest) return operationsHealthRequest.promise;
  const age = Date.now() - operationsHealthObservedAt;
  if (operationsHealthObservedAt !== null && age >= 0
    && age < OPERATIONS_HEALTH_REFRESH_MS) return Promise.resolve();
  window.clearTimeout(operationsHealthTimer);
  operationsHealthTimer = null;
  const generation = ++operationsHealthGeneration;
  const controller = new AbortController();
  currentHealth = currentReadiness = null;
  renderOperations(currentState || UNAVAILABLE_WORKSPACE_PROJECTION);
  const timeout = window.setTimeout(() => controller.abort(), OPERATIONS_HEALTH_TIMEOUT_MS);
  const request = Promise.allSettled([
    api("/api/health", { signal: controller.signal }),
    api("/readyz", { signal: controller.signal }),
  ]).then(([health, readiness]) => {
    if (generation !== operationsHealthGeneration) return;
    currentHealth = !controller.signal.aborted && health.status === "fulfilled" && health.value?.status === "ok"
      ? health.value : { status: "unavailable" };
    currentReadiness = controller.signal.aborted ? { status: "unavailable" } : readinessProbeResult(readiness);
    operationsHealthObservedAt = Date.now();
    renderOperations(currentState || UNAVAILABLE_WORKSPACE_PROJECTION);
  }).finally(() => {
    window.clearTimeout(timeout);
    if (operationsHealthRequest?.promise !== request) return;
    operationsHealthRequest = null;
    if (operationsHealthPageActive && !document.hidden) {
      operationsHealthTimer = window.setTimeout(() => {
        operationsHealthObservedAt = null;
        refreshOperationsHealth();
      }, OPERATIONS_HEALTH_REFRESH_MS);
    }
  });
  operationsHealthRequest = { controller, timeout, promise: request };
  return request;
}

function pauseOperationsHealth() {
  ++operationsHealthGeneration;
  window.clearTimeout(operationsHealthTimer);
  operationsHealthTimer = null;
  window.clearTimeout(operationsHealthRequest?.timeout);
  operationsHealthRequest?.controller.abort();
  operationsHealthRequest = null;
  operationsHealthObservedAt = null;
  currentHealth = currentReadiness = null;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) pauseOperationsHealth();
  else refreshOperationsHealth();
});
window.addEventListener("pagehide", () => {
  operationsHealthPageActive = false;
  pauseOperationsHealth();
});
window.addEventListener("pageshow", () => {
  operationsHealthPageActive = true;
  refreshOperationsHealth();
});
window.addEventListener("hashchange", refreshOperationsHealth);
window.addEventListener("orgrebase:staterendered", refreshOperationsHealth);
window.addEventListener("orgrebase:stateunavailable", refreshOperationsHealth);
refreshOperationsHealth();
