from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from tests.workspace.test_workspace_client import run_node

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "demo" / "console"


def _sources() -> tuple[str, str, str]:
    return (
        (CONSOLE / "index.html").read_text(encoding="utf-8"),
        (CONSOLE / "app.js").read_text(encoding="utf-8"),
        (CONSOLE / "styles.css").read_text(encoding="utf-8"),
    )


def _oac_sources() -> tuple[str, str]:
    return (
        (CONSOLE / "oac-adaptation.js").read_text(encoding="utf-8"),
        (CONSOLE / "oac-adaptation.css").read_text(encoding="utf-8"),
    )


def test_console_defaults_to_chinese_and_exposes_an_accessible_english_switch() -> None:
    html, javascript, css = _sources()

    assert '<html lang="zh-CN">' in html
    assert 'id="language-switcher"' in html
    assert 'role="group"' in html
    assert 'data-i18n-aria-label="language.group"' in html
    assert 'data-language="zh-CN"' in html
    assert 'data-language="en"' in html
    assert 'aria-pressed="true"' in html
    assert 'aria-pressed="false"' in html
    assert 'button.setAttribute("aria-label", label)' in javascript
    assert "button.title = label" in javascript
    assert "document.documentElement.lang = currentLanguage" in javascript
    assert "document.documentElement.dataset.language = currentLanguage" in javascript
    assert ".language-switcher" in css
    assert ".language-option:focus-visible" in css


def test_console_readable_presentation_mode_is_explicit_and_language_independent() -> None:
    _, javascript, css = _sources()
    _, oac_css = _oac_sources()

    assert 'PRESENTATION_QUERY_KEY = "presentation"' in javascript
    assert 'READABLE_PRESENTATION_MODE = "readable"' in javascript
    assert 'new URL(url).searchParams.get(PRESENTATION_QUERY_KEY)' in javascript
    assert 'document.documentElement.dataset.presentation = READABLE_PRESENTATION_MODE' in javascript
    assert 'document.documentElement.removeAttribute("data-presentation")' in javascript
    assert '[data-presentation="readable"] .button' in css
    assert '[data-presentation="readable"] .action-ledger td' in css
    assert '[data-presentation="readable"] .oac-adaptation-summary-copy strong' in oac_css
    assert "--presentation-font-critical: 20px" in css
    assert "--presentation-font-subject: 18px" in css
    assert "--presentation-font-body: 15px" in css
    assert "--presentation-font-context: 13px" in css
    assert "--presentation-font-audit: 12px" in css
    assert "font-size: var(--presentation-font-critical)" in oac_css
    assert "font-size: var(--presentation-font-subject)" in oac_css
    assert "localStorage" not in javascript.split("function applyPresentationMode", 1)[1].split(
        "applyPresentationMode();", 1
    )[0]


def test_console_uses_one_translation_source_for_static_and_dynamic_copy() -> None:
    html, javascript, _ = _sources()

    marker_keys = set(re.findall(r'data-i18n(?:-aria-label)?="([^"]+)"', html))
    runtime_keys = set(re.findall(r'\bt\("([^"]+)"', javascript))
    dictionary_source = javascript.split("let currentLanguage", 1)[0]
    dictionary_counts = Counter(re.findall(r'^\s+"([^"]+)":', dictionary_source, flags=re.MULTILINE))

    assert len(marker_keys) >= 60
    assert marker_keys | runtime_keys
    assert all(dictionary_counts[key] == 2 for key in marker_keys | runtime_keys)
    assert '"zh-CN": Object.freeze({' in javascript
    assert "en: Object.freeze({" in javascript
    assert "AgentTeams" in javascript
    assert "Skill" in javascript
    assert "Tool" in javascript
    assert "run_id" in javascript
    assert "digest" in javascript

    # No Chinese UI copy is embedded in the post-catalog runtime. This keeps
    # dynamic actions, countdowns, errors, gates, and operations bilingual.
    runtime_source = javascript.split("let currentLanguage", 1)[1]
    assert re.search(r"[\u4e00-\u9fff]", runtime_source) is None


def test_language_switch_is_in_place_and_does_not_reset_business_state() -> None:
    _, javascript, _ = _sources()
    switch_body = javascript.split("function setLanguage", 1)[1].split("function stageIndex", 1)[0]

    assert "window.location" not in javascript
    assert ".reload(" not in javascript
    assert "if (currentState) render(currentState)" in switch_body
    assert "renderSemifinalEvidence(currentRetainedEvidence)" in switch_body
    assert "renderPublicRealProcessValidation(currentPublicRealProcessValidation)" in switch_body
    assert "currentState =" not in switch_body
    assert "reviewReadyAt =" not in switch_body
    assert "reviewHoldKey =" not in switch_body
    assert "serverReviewWait =" not in switch_body
    assert "reviewReadyAt - Date.now()" in javascript
    assert 'document.querySelectorAll(".cockpit-tab")' not in switch_body
    assert "openDetailPositions()" in switch_body
    assert "restoreOpenDetails(openDetails)" in switch_body


def test_operations_audit_is_closed_by_default_and_fully_bilingual() -> None:
    html, javascript, _ = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split("const BUSINESS_VALUE_KEYS", 1)[0]

    assert '<details class="ops-audit-details" id="ops-audit-details">' in html
    assert 'id="ops-audit-details" open' not in html
    for chinese in (
        "运行保障详情",
        "可索引遥测留存",
        "被拒载荷诊断留存",
        "不由遥测留存清理删除",
        "异地灾备切换",
    ):
        assert chinese in chinese_catalog
    for english in (
        "Runtime Assurance Details",
        "Indexed telemetry retention",
        "Rejected-payload diagnostic retention",
        "Not deleted by telemetry retention",
        "Geographic disaster-recovery failover",
    ):
        assert english in english_catalog
    assert "openDetailPositions()" in javascript
    assert "restoreOpenDetails(openDetails)" in javascript


def test_current_terminal_projection_boundary_is_explicit_in_both_languages() -> None:
    _, javascript, _ = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split(
        "const BUSINESS_VALUE_KEYS", 1
    )[0]

    for text in (
        "当前运行 · 终态因果投影 · 7 层",
        "浏览器只对当前运行的七层因果结构、关联字段与零写入声明做交叉校验",
        "OTLP 摘要由服务端封口，本页不在浏览器重算",
        "不是实时观测、生产后端或 SLA 证明",
        "冻结档案不会被借用，也不影响已单独校验的业务归档",
    ):
        assert text in chinese_catalog
    for text in (
        "ACTIVE RUN · TERMINAL CAUSAL PROJECTION · 7 LAYERS",
        "The browser only cross-checks the active run's seven-layer causal structure",
        "The server seals the OTLP summary; this page does not recompute its digest",
        "not realtime observation, a production backend, or SLA evidence",
        "frozen evidence is never borrowed",
    ):
        assert text in english_catalog


def test_language_preference_storage_is_non_authoritative_and_fail_soft() -> None:
    _, javascript, _ = _sources()

    stored_body = javascript.split("function storedLanguage", 1)[1].split(
        "function applyStaticTranslations", 1
    )[0]
    switch_body = javascript.split("function setLanguage", 1)[1].split("function stageIndex", 1)[0]
    assert 'LANGUAGE_STORAGE_KEY = "orgrebase.console.language"' in javascript
    assert "window.localStorage.getItem" in stored_body
    assert "catch (_)" in stored_body
    assert "window.localStorage.setItem" in switch_body
    assert "catch (_)" in switch_body
    assert "localStorage" not in javascript.split("async function api", 1)[1]


def test_chinese_surface_localizes_frequent_machine_enums_without_changing_facts() -> None:
    html, javascript, css = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]
    visible_html = re.sub(r"<[^>]+>", " ", html)

    expected = {
        "NOT_RUN": ("enum.notRun", "等待执行"),
        "NOT_OBSERVED": ("enum.notObserved", "未观测"),
        "FAIL": ("enum.fail", "失败"),
        "CRITICAL": ("enum.critical", "严重"),
        "CONTROLLED_LOCAL": ("enum.controlledLocal", "受控本地"),
        "SYNTHETIC_FIXTURE": ("enum.syntheticFixture", "受控演示数据"),
        "SINGLE_RUN_SEED": ("enum.singleRunSeed", "单次运行经验种子"),
        "CANDIDATE_ACCEPTED": ("status.candidateAcceptedCode", "候选已验收"),
        "GOVERNED_APPLIED": ("status.governedAppliedCode", "受治理状态已写入"),
        "CONTROLLED_LOCAL_SCRIPTED_COMMAND": (
            "status.controlledLocalCommandCode",
            "受控本地脚本化负责人命令",
        ),
        "ABSTAIN": ("enum.abstain", "主动放弃交付"),
        "REPLAN": ("enum.replan", "重新规划"),
        "ADVISORY_ACCEPTED": ("enum.advisoryAccepted", "建议候选已接纳"),
        "REVIEW_REQUIRED": ("enum.reviewRequired", "需要人工复核"),
    }
    for raw, (key, chinese) in expected.items():
        assert f'{raw}: "{key}"' in javascript
        assert f'"{key}": "{chinese}"' in chinese_catalog
        assert raw not in visible_html

    assert "localizedFactHtml(row.action)" in javascript
    assert "localizedFactHtml(row.status)" in javascript
    assert "localizedFactHtml(row.writes)" in javascript
    assert (
        '"lifecycle.authorityBoundary": "AgentTeams 已接收 ≠ 控制面已准入 ≠ 人工已批准 ≠ 规范状态已写入"'
        in chinese_catalog
    )
    assert '"lifecycle.step.project": "在依赖图中创建任务"' in chinese_catalog
    assert '"lifecycle.step.received": "AgentTeams 已接收"' in chinese_catalog
    assert '"lifecycle.step.terminal": "项目终态"' in chinese_catalog
    assert "Manager role" not in chinese_catalog
    assert '.machine-token::before { content: "["; }' in css
    assert '.machine-token::after { content: "]"; }' in css


def test_chinese_surface_localizes_business_values_and_hides_review_workbench_artifacts() -> None:
    html, javascript, _ = _sources()
    oac_javascript, _ = _oac_sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split("const BUSINESS_VALUE_KEYS", 1)[0]

    business_mappings = {
        "customer:blue-harbor": ("business.customer.blueHarbor", "蓝港客户"),
        "enterprise-quote-operator": ("business.role.quoteOperator", "报价运营负责人"),
        "Blue Harbor Enterprise Quote": ("business.quote.blueHarbor", "蓝港企业报价"),
        "Evergreen Enterprise Plus": ("business.plan.evergreenPlus", "常青企业增强版"),
        "US and EU regions supported": ("business.residency.usEu", "支持美国与欧盟区域驻留"),
        "COMPLETE_COVERAGE_NO_ADMITTED_PATH": (
            "business.reason.coverageNoPath",
            "依赖覆盖完整，未发现被采纳的影响路径",
        ),
        "DEPENDENCY_COVERAGE_INSUFFICIENT": (
            "business.reason.insufficientCoverage",
            "依赖证据不足，必须人工复核",
        ),
        "human:skill-steward": ("business.role.skillSteward", "Skill 治理负责人"),
        "ORGREBASE_CONTROL_PLANE": (
            "authority.canonical",
            "OrgRebase 规范状态控制面",
        ),
        "STATESTORE_REBASE_WORKFLOW_ONLY": (
            "authority.stateStoreOnlyCode",
            "OrgRebase 规范状态控制面是唯一写入权威",
        ),
    }
    for raw, (key, chinese) in business_mappings.items():
        assert f'"{raw}": "{key}"' in javascript or f'{raw}: "{key}"' in javascript
        assert f'"{key}": "{chinese}"' in chinese_catalog

    assert 'localizedText("profile-data-badge"' in javascript
    assert "{ showRaw: false }" in javascript
    assert "function localizedBusinessText" in javascript
    assert 't("action.dynamic.start", { label: displayToken(scenario.label) })' in javascript
    assert "displayToken(item.reason_code" in javascript
    assert "BPI 2019 公开真实采购到付款机制验证" in html
    assert "BPI Challenge 2019 是真实匿名跨国企业" in html
    assert "官方真实日志 → 摘要固定投影 → OAC 类型化作用域 → 独立离线复验" in html
    assert "公开真实日志 → 受控 AgentTeams 候选映射 → 确定性 OAC 校验" in chinese_catalog
    assert "负责人审阅门 → 128 查询执行 → 独立复验" in chinese_catalog
    assert "4 类未知 / 暂停" not in chinese_catalog
    assert "{count} 项必须由企业声明：{dimensions}" in chinese_catalog
    assert "UNKNOWN / HOLD" not in chinese_catalog
    assert "UNKNOWN / HOLD" not in english_catalog
    assert "{count} facts must be declared by the enterprise: {dimensions}" in english_catalog
    assert "提前脚本指令已拒绝 · {elapsed} 秒后准入" in chinese_catalog
    assert "未证明外部真人验收" in chinese_catalog
    assert "自动适配任意企业" in chinese_catalog
    assert "public real log → governed AgentTeams candidate mapping" in english_catalog
    assert "external-human acceptance was not proven" in english_catalog
    assert "OAC 类型化作用域（机制验证）" in html
    assert "受控合成企业样例 · 可操作验证" in oac_javascript
    assert "组队决策收据已绑定" in oac_javascript
    assert "manager.formation_decision_receipt_digest" in oac_javascript
    assert "尚未生成" in chinese_catalog
    assert "此代码包未包含可读取的BPI采购流程记录" in chinese_catalog
    assert "模型准确率" in chinese_catalog
    assert "PUBLIC_REAL_PROCESS_RULE_DERIVED_MECHANISM_VALIDATION" not in html
    assert "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG" not in html
    for artifact in (
        "semifinal-rubric",
        "judge-checklist",
        "goldenJudgeGates",
        "rubric.",
        "gate.p0",
        "gate.p1",
        "复赛证据门禁",
        "ACTIVE-RUN SEMIFINAL EVIDENCE GATES",
    ):
        assert artifact not in html
        assert artifact not in javascript


def test_dynamic_formation_reconciliation_is_bilingual_and_fail_closed() -> None:
    _, javascript, _ = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split("const BUSINESS_VALUE_KEYS", 1)[0]

    for text in (
        "动态组队计划对账",
        "动态组队计划 {planned} 个领域 = AgentTeams 实际 {actual} 个领域",
        "候选态 · 0 次规范写入 · 独立固定版本进程内复验",
        "动态组队证据不可用 · 保持关闭 · 未展示计划或实际拓扑",
    ):
        assert text in chinese_catalog
    for text in (
        "DYNAMIC FORMATION PLAN RECONCILIATION",
        "Formation planned {planned} domains = AgentTeams ran {actual} domains",
        "candidate-only · 0 canonical writes · independent pinned in-process replay",
        "Dynamic formation evidence unavailable · fail closed",
    ):
        assert text in english_catalog

    assert "collaboration.formation_taskflow" in javascript
    assert "plannedDomains.every" in javascript
    assert "reviewerDependencies.every" in javascript
    assert 'formation.status === "PASS"' in javascript
    assert "formation.canonical_target_writes === 0" in javascript
    assert "function renderFormationTopology" in javascript
    assert 'renderDirectedTopology("formation-agent-topology"' in javascript
    assert "domainTasks.map" in javascript
    assert "AUTHORITY, CONFLICT & RESULT FENCING" in english_catalog
    assert "DOES NOT REPEAT THE ACTIVE BUSINESS FLOW" in english_catalog


def test_business_data_and_authority_views_are_fully_bilingual() -> None:
    html, javascript, _ = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split("\n  }),\n});", 1)[0]

    assert 'data-i18n="cockpit.tab.scene">业务与数据</button>' in html
    for chinese in (
        "同一变化的预演与应用分开呈现",
        "系统没有全量重做",
        "初始形成与本次变更分别核对",
        "从团队交付到规则生效",
        "审查智能体已验收",
        "技术证据详情",
        "静态处理规则",
    ):
        assert chinese in chinese_catalog
    for english in (
        "Business & Data",
        "Preview and Apply are distinct stages of the same change",
        "The system did not redo everything",
        "FORMATION AND THIS CHANGE HAVE DISTINCT EVIDENCE",
        "From team delivery to rules taking effect",
        "Reviewer accepted",
        "TECHNICAL EVIDENCE DETAILS",
        "static handling rules",
    ):
        assert english in english_catalog

    assert "function renderBusinessChangeBoard" in javascript
    assert "function renderAuthorityLadder" in javascript

    for catalog in (chinese_catalog, english_catalog):
        for line in (candidate for candidate in catalog.splitlines() if "run_id" in candidate):
            assert (
                '"currentArchive.' in line
                or '"archive.frozen.boundary"' in line
                or '"publicValidation.adaptation.runBoundary"' in line
                or '"publicValidation.runSummary"' in line
                or '"proofOverview.boundary"' in line
            )
    assert "独立审查智能体" in chinese_catalog
    assert "Independent Reviewer" in english_catalog
    assert "报价任务协调器" in chinese_catalog
    assert "Quote Task Coordinator" in english_catalog
    assert "已绑定本轮预演" in chinese_catalog
    assert "BOUND TO THIS PREVIEW" in english_catalog


def test_enterprise_data_journey_is_bilingual_readable_and_product_clean() -> None:
    html, javascript, css = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split("\n  }),\n});", 1)[0]
    renderer = javascript.split("function renderEnterpriseDataJourney", 1)[1].split(
        "function renderBusinessChangeBoard", 1
    )[0]

    assert 'data-i18n="dataJourney.title">变化影响与报价后继版本' in html
    assert 'data-i18n="dataJourney.oac.title">OAC 约束已绑定' in html
    assert 'data-i18n="dataJourney.context.title">OrgRebase 最小上下文' in html
    for chinese in (
        "变化影响与报价后继版本",
        "审批前候选影响与审批后选择性重构",
        "OAC 负责准入组织材料并约束本次任务与最小上下文",
        "形成权威：OrgRebase 控制面任务上下文编译器",
        "受控实例：证明结构闭环",
        "公开流程：独立验证跨流程映射",
        "企业试点：真实连接器与 ROI 需在企业环境验证",
        "确定性报价组装器",
        "已登记业务来源",
        "本轮回执未完整观测",
    ):
        assert chinese in chinese_catalog
    for english in (
        "Change Impact and Quote Successor Versions",
        "Pre-approval candidate impact and post-approval selective Rebase",
        "OAC admits organization material and constrains this task and its least-privilege context",
        "Formation authority: OrgRebase control-plane task context compiler",
        "CONTROLLED INSTANCE: STRUCTURAL LOOP PROOF",
        "PUBLIC PROCESS: INDEPENDENT CROSS-PROCESS MAPPING VALIDATION",
            "ENTERPRISE PILOT: CONNECTORS AND ROI REQUIRE ENTERPRISE VALIDATION",
        "Deterministic quote renderer",
        "Registered business source",
        "This round's receipt is not fully observed",
    ):
        assert english in english_catalog

    for forbidden_product_copy in (
        "评委",
        "评分",
        "Spec 074",
        ".json",
        "raw_private_value",
        "lineage.run_id",
        "lineage.digest",
        "workspace-renderer",
        "OAC Runtime",
    ):
        assert forbidden_product_copy not in renderer
    assert "exactRefs" not in renderer
    assert 'title="${escapeHtml' not in renderer

    for readable_selector in (
        '[data-presentation="readable"] .enterprise-data-journey > header strong',
        '[data-presentation="readable"] .enterprise-data-journey-step > strong',
        '[data-presentation="readable"] .enterprise-data-source-values strong',
        '[data-presentation="readable"] .enterprise-data-journey-changes article > header strong',
    ):
        assert readable_selector in css
    assert "font-size: var(--presentation-font-critical)" in css
    assert "font-size: var(--presentation-font-subject)" in css


def test_chinese_primary_copy_uses_natural_terms_and_marks_vertex_receipt_as_frozen() -> None:
    _, javascript, _ = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]

    for half_localized in (
        "not_before",
        "target writes",
        "controlled-local",
        "Owner 不匹配",
        "Workspace 就绪状态",
        "形成 Quote",
        "当前 Golden Run",
        "批准进入 CANARY",
        "fail closed",
        "Live model advisory",
        "Golden 运行",
        "独立受控本地机制运行",
        "Evidence Index",
        "AS-IS",
        "隐私 canary",
        "native task lifecycle",
        "reviewer barrier",
    ):
        assert half_localized not in chinese_catalog

    assert "服务端四秒审阅门尚未到时" in chinese_catalog
    assert "规范写入" in chinese_catalog
    assert "真实 Vertex 模型建议" in chinese_catalog
    assert "已冻结回执" in chinese_catalog
    assert "本地验证环境" in chinese_catalog
    assert "协作引擎：AgentTeams" in chinese_catalog
    assert "生产就绪：否 · 待企业验证" in chinese_catalog
    assert "PRODUCTION READY: NO · ENTERPRISE VALIDATION PENDING" in javascript
    assert "报价 v1 基线组队回看" in chinese_catalog
    assert "当前 Golden run" not in chinese_catalog
    assert "QUOTE V1 BASELINE FORMATION REPLAY" in javascript
    assert "CURRENT FROZEN BUSINESS RUN" not in javascript
    assert "本机顺序容量基础验证" in chinese_catalog
    assert "非并发 / 非 SLA" in chinese_catalog
    assert "企业系统连接器需部署时配置" in chinese_catalog
    assert "供应链签名需企业环境验收" in chinese_catalog
    assert "证据索引 · 冻结独立验证档案" in chinese_catalog
    assert "隐私探针 / 拒绝" in chinese_catalog
    assert "八步流程用于现状人工基线" in chinese_catalog
    assert "Captured real Vertex model advisory" in javascript


def test_oac_adaptation_add_on_is_bilingual_without_owning_business_state() -> None:
    html, javascript, _ = _sources()
    oac_javascript, oac_css = _oac_sources()

    assert 'href="/assets/styles.css?v=0.4.0-13"' in html
    assert 'href="/assets/oac-adaptation.css?v=0.4.0-13"' in html
    assert 'href="/assets/workspace-shell.css?v=0.4.0-13"' in html
    assert 'src="/assets/app.js?v=0.4.0-15"' in html
    assert 'src="/assets/oac-adaptation.js?v=0.4.0-14"' in html
    assert 'src="/assets/workspace-shell.js?v=0.4.0-13"' in html
    assert 'id="oac-preflight-mount"' in html
    assert 'id="oac-adaptation"' in html
    assert '"zh-CN": Object.freeze({' in oac_javascript
    assert "en: Object.freeze({" in oac_javascript
    assert "orgrebase:languagechange" in javascript
    assert "orgrebase:languagechange" in oac_javascript
    assert 'event.detail.language === "en"' in oac_javascript
    assert "currentAdaptation = null" in oac_javascript
    language_handler = oac_javascript.split('window.addEventListener("orgrebase:languagechange"', 1)[1].split(
        "const preflightMount", 1
    )[0]
    assert "currentAdaptation =" not in language_handler
    assert 'byId("oac-verification-evidence").open' not in language_handler
    assert "window.location" not in oac_javascript
    assert ".reload(" not in oac_javascript
    assert "localStorage" not in oac_javascript
    assert ".oac-adaptation > summary:focus-visible" in oac_css
    assert "prefers-reduced-motion" in oac_css

    chinese_catalog = oac_javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[
        0
    ]
    english_catalog = oac_javascript.split("en: Object.freeze({", 1)[1].split("\n    }),\n  });", 1)[0]
    assert "run_id" not in chinese_catalog
    assert "run_id" not in english_catalog
    for chinese in (
        "高级审计证据",
        "业务启动门",
        "本次报价已消费 OAC",
        "企业事实缺口",
        "契约与人工准入已完成",
        "影子运行与独立复核已通过",
        "真实 Vertex 模型建议",
        "本地模型建议",
        "模型建议回执",
    ):
        assert chinese in chinese_catalog
    for boundary in (
        "当前模式：{mode}",
        "候选来源：{source}",
        "只把已观测的模型回执显示为真实运行",
        "接入智能体只提议",
        "OAC 校验与指定负责人决定是否激活",
    ):
        assert boundary in chinese_catalog
    for english in (
        "Advanced audit evidence",
        "BUSINESS START GATE",
        "THIS QUOTE CONSUMED OAC",
        "ENTERPRISE FACT GAPS",
        "Contract and human admission complete",
        "Shadow run and independent verification passed",
        "Live Vertex model advisory",
        "Local model advisory",
        "Model advisory receipt",
    ):
        assert english in english_catalog

    language_switch = javascript.split("function setLanguage", 1)[1].split("function stageIndex", 1)[0]
    assert "currentOacWorkspaceGate =" not in language_switch
    assert '"action.oacGate.title": "组织契约未准入，报价暂未放行"' in javascript
    assert '"action.oacGate.title": "The organizational contract is not admitted; quote formation is paused"' in javascript


def test_shell_copy_distinguishes_actual_execution_from_controlled_sample_data() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    chinese_catalog = shell.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    english_catalog = shell.split("en: Object.freeze({", 1)[1]

    for copy in (
        "业务生命周期",
        "变化处置",
        "企业接入",
        "能力与保障",
        "30 秒变化摘要",
        "组织背景：受控企业样例 · 仅验证结构闭环",
        "来源已准入并与运行配置匹配",
        "员工需求已确认，智能体团队已启动",
        "校验工作说明并生成候选",
        "受约束任务候选",
        "任务发起证据不可用",
        "持续治理与保障",
        "能力中心",
        "任务验收",
        "运行记录与验证",
        "运行保障",
        "可审计",
    ):
        assert copy in chinese_catalog
    assert "由员工发起一项真实工作" not in chinese_catalog
    assert "真实输入" not in chinese_catalog

    for copy in (
        "BUSINESS LIFECYCLE",
        "Change Response",
        "Enterprise Onboarding",
        "Capabilities & Assurance",
        "30-SECOND CHANGE SUMMARY",
        "ORGANIZATION: CONTROLLED ENTERPRISE SAMPLE · STRUCTURAL LOOP ONLY",
        "Source admitted and matched to runtime configuration",
        "Validate description and create candidate",
        "CONSTRAINED TASK CANDIDATE",
        "TASK INTAKE EVIDENCE UNAVAILABLE",
        "CONTINUOUS GOVERNANCE & ASSURANCE",
        "Capability Center",
        "Task Acceptance",
        "Run records & validation",
        "Runtime Assurance",
        "AUDIT",
    ):
        assert copy in english_catalog


def test_pre_task_process_baseline_is_bilingual_and_not_claimed_as_run_evidence() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    chinese_catalog = shell.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    english_catalog = shell.split("en: Object.freeze({", 1)[1]

    assert (
        'processBaselineTitle: "八步现状流程与职责分工（RACI）：人工基线待企业校准，不是系统运行耗时"'
        in chinese_catalog
    )
    assert "系统实测结果只来自同运行回执，人工等待时间在审批门单独显示" in chinese_catalog
    assert (
        'processBaselineTitle: "Eight-step current-state process and RACI: human baseline pending enterprise calibration, not system runtime"'
        in english_catalog
    )
    assert "System measurements come only from same-run receipts; human waiting time is shown separately at approval gates" in english_catalog
    for key in (
        "processBaselineKicker",
        "processBaselineTitle",
        "processBaselineBoundary",
        "acceptanceProcessDetails",
    ):
        assert chinese_catalog.count(f"{key}:") == 1
        assert english_catalog.count(f"{key}:") == 1


def test_state_driven_product_copy_distinguishes_task_gate_and_recorded_oac_admission() -> None:
    shell = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    oac = (CONSOLE / "oac-adaptation.js").read_text(encoding="utf-8")
    shell_chinese = shell.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    shell_english = shell.split("en: Object.freeze({", 1)[1]
    oac_chinese = oac.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    oac_english = oac.split("en: Object.freeze({", 1)[1]

    # The third task-candidate field describes three distinct states instead of
    # presenting a satisfied OAC binding as an unresolved item.
    for key in (
        "taskConstraintLabel",
        "taskStartPrerequisitesLabel",
        "taskHoldReasonLabel",
        "taskNoUnknowns",
    ):
        assert key in shell_chinese
        assert key in shell_english
    third_field = shell.split('byId("task-intake-third-label").textContent =', 1)[1].split(
        'byId("task-intake-unknowns").removeAttribute', 1
    )[0]
    assert "formed" in third_field
    assert "candidateHold" in third_field
    assert "taskConstraintLabel" in third_field
    assert "taskHoldReasonLabel" in third_field
    assert "taskStartPrerequisitesLabel" in third_field
    assert "taskNoUnknowns" in third_field

    # Once onboarding is complete, the CTA and acknowledgement block describe
    # durable recorded state rather than inviting the user to redo admission.
    assert "viewActiveOac" in shell_chinese
    assert "viewActiveOac" in shell_english
    assert (
        "onboardingContext(state).digest ? selected.viewActiveOac : selected.openOac"
        in shell
    )
    for key in ("acknowledgementsRecordedTitle", "acknowledgementsRecordedNote"):
        assert key in oac_chinese
        assert key in oac_english
    acknowledgement_render = oac.split(
        'const acknowledgementBox = byId("oac-review-acknowledgements")', 1
    )[1].split("const recorded = new Set", 1)[0]
    assert "approved ? c.acknowledgementsRecordedTitle : c.acknowledgementsTitle" in acknowledgement_render
    assert "approved ? c.acknowledgementsRecordedNote : c.acknowledgementsNote" in acknowledgement_render


def test_operations_terms_localize_control_plane_and_preserve_external_write_boundary() -> None:
    _, javascript, _ = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split(
        "\n  }),\n});", 1
    )[0]

    for key in (
        "operations.sameRun.externalTargetWrites",
        "plane.control",
    ):
        assert f'"{key}"' in chinese_catalog
        assert f'"{key}"' in english_catalog
    assert 'CONTROL: "plane.control"' in javascript
    assert 't("operations.sameRun.externalTargetWrites", { count: item.target_writes })' in javascript
    assert 'operations.card.frozenPending' not in javascript
    assert 'operations.card.frozenPendingComplete' not in javascript
    assert "0 次外部目标写入" in chinese_catalog
    assert "0 EXTERNAL TARGET WRITES" in english_catalog


def test_oac_bound_execution_copy_has_matching_chinese_and_english_catalog_keys() -> None:
    _, javascript, _ = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1].split(
        "\n  }),\n});", 1
    )[0]
    new_keys = (
        "dataJourney.oac.summaryBound",
        "dataJourney.oac.boundaryPending",
        "topology.boundary.activeOac",
        "topology.stage.oac",
        "topology.role.oacFormation",
        "topology.name.oacFormation",
        "topology.status.oacFormation",
        "topology.detail.oacCompiler",
        "topology.detail.logicalEventTime",
    )

    for key in new_keys:
        assert chinese_catalog.count(f'"{key}"') == 1
        assert english_catalog.count(f'"{key}"') == 1
    for stage_number, key in enumerate(
        (
            "topology.stage.oac",
            "topology.stage.manager",
            "topology.stage.domains",
            "topology.stage.recovery",
            "topology.stage.skill",
            "topology.stage.human",
            "topology.stage.canonical",
        ),
        start=1,
    ):
        assert f'"{key}": "{stage_number:02d} ·' in chinese_catalog
        assert f'"{key}": "{stage_number:02d} ·' in english_catalog


def test_oac_chinese_copy_has_no_unconditional_mode_or_half_translated_shadow_claims() -> None:
    html, javascript, _ = _sources()
    oac_javascript, _ = _oac_sources()
    chinese_catalog = oac_javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[0]
    visible_fallback = re.sub(r"<[^>]+>", " ", html)

    for stale_copy in (
        "ERP / Pricing",
        "OAC-bound Shadow",
        "新跑 Shadow",
        "Gemini 候选",
    ):
        assert stale_copy not in chinese_catalog
        assert stale_copy not in visible_fallback

    assert "企业资源计划（ERP）/ 定价系统" in javascript
    assert "OAC 定义智能体必须遵守的输入、权限、交接与准入规则" in visible_fallback
    assert 'id="oac-primary-action" type="button" disabled' in html


def test_product_surface_does_not_expose_bare_not_run_labels() -> None:
    html, javascript, _ = _sources()
    oac_javascript, _ = _oac_sources()
    shell_javascript = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    visible_fallback = re.sub(r"<[^>]+>", " ", html)

    for forbidden in ("未运行", "尚未运行", "NOT RUN"):
        assert forbidden not in visible_fallback
        assert forbidden not in shell_javascript
    assert '"enum.notRun": "等待执行"' in javascript
    assert 'NOT_RUN: "等待模型候选"' in oac_javascript
    assert "需企业环境验收" in javascript


def test_workspace_shell_uses_localized_capability_copy_and_valid_page_labels() -> None:
    html, javascript, _ = _sources()
    shell_javascript = (CONSOLE / "workspace-shell.js").read_text(encoding="utf-8")
    visible_fallback = re.sub(r"<[^>]+>", " ", html)

    assert 'data-shell-copy="skillCenterBadge">Skill 治理<' in html
    assert "Skill Governance" not in visible_fallback
    assert 'skillCenterBadge: "Skill 治理"' in shell_javascript
    assert 'skillCenterBadge: "Skill Governance"' in shell_javascript
    assert 'role: "tablist"' in shell_javascript
    assert 'data-shell-aria-label": ariaCopyKey' in shell_javascript
    assert 'role="tablist" aria-label="Workspace lifecycle"' not in shell_javascript
    assert 'button.setAttribute("aria-selected", active ? "true" : "false")' in shell_javascript
    assert 'data-i18n-aria-label="a11y.oacReviewOverview"' in html
    assert '"a11y.oacReviewOverview": "Organization-contract review overview"' in _sources()[1]
    assert (
        "本次任务未绑定独立的 Skill 发布生命周期汇总；"
        "下方已发布原文直接来自能力注册表。"
    ) in javascript
    assert (
        "This task has no separate Skill-release lifecycle summary; "
        "the published sources below come directly from the capability registry."
    ) in javascript
    assert '"aria-labelledby": "shell-page-title"' in shell_javascript
    assert "shell-title-${id}" not in shell_javascript
    assert 'node.getAttribute("role") === "tabpanel"' in shell_javascript
    assert 'node.removeAttribute("aria-labelledby")' in shell_javascript


def test_language_switch_preserves_sidebar_route_labels_when_shell_has_active_route() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=require('node:fs').readFileSync('demo/console/workspace-shell.js','utf8');
const routes=['onboarding','quote','assurance'];
const buttons=routes.map(route=>({className:'shell-nav-item',dataset:{route},children:[{className:'shell-nav-label',dataset:{},children:[],textContent:''}]}));
const shell={className:'enterprise-app-shell',dataset:{route:'onboarding'},children:buttons};
const walk=node=>[node,...node.children.flatMap(walk)];
function matches(node,selector){
 const className=selector.match(/^\.([\w-]+)/)?.[1];
 const route=selector.match(/\[data-route="([^"]+)"\]/)?.[1];
 return (!className||node.className===className)&&(!route||node.dataset.route===route);
}
for(const node of walk(shell))node.querySelector=selector=>node.children.flatMap(walk).find(child=>matches(child,selector))||null;
const document={querySelector:selector=>walk(shell).find(node=>matches(node,selector))||null,querySelectorAll:()=>[]};
const context=vm.createContext({document,currentState:null,language:'zh-CN',copy:()=>vm.runInContext('COPY[language]',context),
 renderOverviewFlow(){},renderLifecycle(){},renderLifecycleRail(){},renderRouteHeader(){},renderState(){}});
vm.runInContext(source.slice(source.indexOf('const ROUTES'),source.indexOf('const byId')),context);
vm.runInContext(source.slice(source.indexOf('function setCopy()'),source.indexOf('function stageIndex')),context);
const expected={'zh-CN':['企业接入','变化处置','能力与保障'],en:['Enterprise Onboarding','Change Response','Capabilities & Assurance']};
for(const route of routes){
 shell.dataset.route=route;
 for(const language of ['zh-CN','en','zh-CN','en']){
   context.language=language;context.setCopy();
   assert.deepEqual(buttons.map(button=>button.children[0].textContent),expected[language],`${route}: ${language}`);
   assert.deepEqual(buttons.map(button=>button.dataset.route),routes,'language changes do not retarget navigation');
 }
}
''')


def test_console_labels_frozen_proof_and_skill_ids_without_confusing_them_with_current_work() -> None:
    html, javascript, css = _sources()
    chinese_catalog = javascript.split('"zh-CN": Object.freeze({', 1)[1].split(
        "en: Object.freeze({", 1
    )[0]
    english_catalog = javascript.split("en: Object.freeze({", 1)[1]

    assert "发布样例业务闭环（冻结回执，非当前任务）" in html
    assert '"proofOverview.kicker": "历史发布验证"' in chinese_catalog
    assert "Quote workflow · Historical run" in english_catalog
    for display_name in (
        "企业报价组装 Skill",
        "结构化领域交接 Skill",
        "企业上线准备度 Skill",
    ):
        assert display_name in chinese_catalog
    assert "function skillDisplayName(name)" in javascript
    assert 'skillSourceElement("strong", "", skillDisplayName(skillPackage.name))' in javascript
    assert '<strong>${escapeHtml(skillDisplayName(skill.name))}</strong>' in javascript
    assert 'class="skill-stable-id"' in javascript
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in css
    assert ".timeline li:nth-child(-n+4)" in css


def test_oac_chinese_copy_translates_boundaries_but_preserves_machine_tokens() -> None:
    html, _, _ = _sources()
    oac_javascript, _ = _oac_sources()
    chinese_catalog = oac_javascript.split('"zh-CN": Object.freeze({', 1)[1].split("en: Object.freeze({", 1)[
        0
    ]
    visible_fallback = re.sub(r"<[^>]+>", " ", html)

    for natural_copy in (
        "零外部效应",
        "规范写入",
        "人工权威",
        "企业合同负责人",
        "默认拒绝",
        "尚未绑定",
        "形成一份零外部效应的受治理企业报价",
    ):
        assert natural_copy in chinese_catalog

    assert (
        'objective === "produce one governed enterprise quote with zero external effects"' in oac_javascript
    )

    for stable_token in (
        "OAC",
        "OrgRebase",
        "Agent",
        "Model",
        "Tool",
        "Source",
        "Demand",
        "Context",
    ):
        assert stable_token in oac_javascript

    for raw_token in (
        "oac_runtime_invoked=false",
        "oac_plan_produced=false",
        "adaptation_run",
        "capsule",
        "execution_run",
        "human:workspace-owner",
    ):
        assert raw_token not in visible_fallback

    for product_copy in (
        "企业材料",
        "接入智能体",
        "OAC 契约校验",
        "上下文编译",
        "适配运行 → 适配胶囊 → 执行运行",
        "企业工作台负责人",
    ):
        assert product_copy in visible_fallback

    for internal_copy in ("Golden 运行", "Golden 证据", "Intake Agent", "Intake-Agent"):
        assert internal_copy not in chinese_catalog


def test_oac_compound_blockers_keep_the_source_anchor_explanation() -> None:
    run_node(r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=fs.readFileSync('demo/console/oac-adaptation.js','utf8');
const context=vm.createContext({copy:()=>({contextUnanchored:'Source evidence is not anchored',
 blockedByPolicy:{OFFLINE_LOCAL:'Local mode blocked',UNKNOWN_SAFE:'Unknown policy'}})});
for(const [start,end] of [
 ['  function policyActionToken(', '  function normalizeExecutionPolicy('],
 ['  function policyBlockReason(', '  function policyActionForStatus('],
])vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),context);
const anchor='OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_UNANCHORED';
for(const reasons of [[anchor],['OAC_AGENTIC_RUNTIME_LOCAL_BLOCKED',anchor],[anchor,'OAC_AGENTIC_RUNTIME_LOCAL_BLOCKED']]){
 const blockedReasons=context.normalizeBlockedReasons({AGENT_PREPARE:reasons});
 const result=context.policyBlockReason({executionPolicy:{mode:'OFFLINE_LOCAL',blockedReasons}},'AGENT_PREPARE');
 assert.equal(result.naturalReason,'Source evidence is not anchored');
 assert.equal(result.stableReason,reasons.join(' · '),'all backend reasons remain available');
}
const result=context.policyBlockReason({executionPolicy:{mode:'OFFLINE_LOCAL',blockedReasons:{AGENT_PREPARE:anchor+'_OTHER'}}},'AGENT_PREPARE');
assert.equal(result.naturalReason,'Local mode blocked','only a complete reason token gets the anchor explanation');
''')
