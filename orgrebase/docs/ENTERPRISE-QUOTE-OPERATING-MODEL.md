# 企业报价变化运营闭环

> 这是 [L0 产品真相](SYSTEM-MAP.md) 的单一用户与价值口径 L1，不是对真实企业现状的已完成调研。

**主用户**：Quote Operations Owner / 报价变更负责人

**主交付物**：一份可交付、可追溯、变化后可选择性更新的 Enterprise Quote

**当前证据上限**：`SYNTHETIC_CONTROLLED_VALUE_PROOF`

**真实企业 ROI**：`NOT_RUN`

## 1. 产品切口与基座的关系

OrgRebase 不是报价生成器，也不是通用 Agent 的另一个实现。它解决的是：上游产品事实、
法务义务、财务政策或 GTM 约束发生变化后，企业如何找到仍依赖旧事实的成果，自动形成
最小跨域 Agent 团队，先完成候选修正与证据验收，再只在精确权威点通知人并选择性更新。
Enterprise Quote 是当前可运行验证切片；OAC 是底层标准，用来定义 Agent、知识、工具、
权限、交接、产出和证据如何构成一个可接受的动态组织。

这两层各自有明确验收对象：

| 层 | 验收对象 | 当前可辩护结论 |
|---|---|---|
| 变化驱动验证切片 | Quote、WorkTrace、VMRC、审批与后继 Quote | 本地受控流程已运行，业务价值仅能使用代理指标和归一化反事实模型 |
| OAC 基座 | 企业接入、候选映射、精确 Owner 准入与任务约束 | 当前 Golden Quote 已在同一 run 消费不可变 OAC 激活绑定；通用 OAC 参考演化仍是独立证据泳道 |

当前报价主链已共享同一 `run_id` 的 OAC 激活绑定、任务形成、AgentTeams、
Tool、Skill、人工门和 Quote 后继版本。但 OAC 的通用 Plan / Outcome / Source 演化
参考旅程仍是另一条冻结 run；它只说明标准可组合性，不得拼入当前报价的业务因果。

## 2. 现状流程的 Shadow Pilot 采集模板

下表是用于 Shadow Pilot 的**人工现状采集框架**，不是对某家企业的调研结论。“As-is 系统”
只表示接口类别，不代表 Salesforce、SAP、Ironclad 或其他具名 SaaS 已经接通。表中时限是
待企业试点验证的参考值，状态统一为 `NOT_RUN`；它们既不是 Agent 运行时间，也不是已达成 SLA。

| 步骤 | As-is 接口类 | To-be 责任 | 待测人工时限（`NOT_RUN`） | R | A | 审批或终态 |
|---|---|---|---:|---|---|---|
| 需求接收 | CRM / CPQ request queue | 请求适配与入场检查 | 30 min | Quote Operations Owner | GTM Owner | 入场或结构化退回 |
| 产品确认 | Product catalog / release source | Product 领域投影 | 4 business h | Product Agent | Product Owner | 精确版本引用被承认 |
| 法务确认 | CLM / restricted legal source | 用途约束的 Legal 投影 | 1 business d | Legal Agent | Legal Owner | 派生义务获批，受限原文不外泄 |
| 财务确认 | ERP / pricing policy source | Finance 领域投影 | 4 business h | Finance Agent | Finance Owner | 政策版本、币种和价格带获批 |
| 报价组装 | Document / CPQ composer | Workspace 组装与证据绑定 | 30 min after inputs | GTM Agent | Quote Operations Owner | Quote + WorkTrace 完整 |
| 交付验收 | CPQ / document output | 受治理导出 | 1 business d | Quote Operations Owner | GTM Owner | 对客交付或返工 |
| 变化影响 | Manual cross-system review | 确定性控制面 | 30 min | Deterministic Control | Changed-source Owner | VMRC；`UNKNOWN` 必须升级 |
| 选择性更新 | Manual document/system rework | Bounded Runtime + Canonical Writer | 2 business h | Runtime / Writer | Exact Approval Owner | 后继 Quote 与 Graph 原子提交 |

完整的输入、输出、RACI、失败路径与系统边界受
[`current-process-baseline.json`](../benchmark/quote-value-v0.1/public/current-process-baseline.json)
和
[`workspace-current-process-baseline.schema.json`](../schemas/workspace-current-process-baseline.schema.json)
约束。每一步只能有一个 Accountable；Agent Worker、Human Owner 和 Canonical Writer 不合并为
一个含糊的“Agent 负责”。

## 3. 从现状到目标状态

```text
As-is 参考流程
CRM/CPQ → 人工跨 Product/Legal/Finance 追询 → 文档拼装 → GTM 验收
                       变化后：人工查找、重做或遗漏

To-be 受治理流程
Request → Contract/Authority admission → Domain projections → Quote + Trace
                                                        ↓
ChangeSet → frozen universe → Preview/VMRC → exact approval → selective successor + atomic receipt
```

核心不是让 Agent 任意自动化，而是把动态组队、最小上下文、权限和变化恢复放进一组可验证
契约。Domain Agent 自动完成候选层任务；Reviewer 验收候选证据；确定性控制面计算影响、冻结
Preview/VMRC 并在需要规范写入时通知精确 Human Owner。人工不逐步设计或执行 Agent 任务，
只在 canonical authority gate 决策；批准后由唯一写入器自动完成选择性 Rebase。

三类时间必须分开报告：

1. `AS_IS_NOT_RUN`：上表人工流程的待测参考值；
2. `OBSERVED_LOCAL_SYSTEM`：本地受控运行中实际记录的 Agent/Tool/Skill/控制面处理时间；
3. `EXTERNAL_HUMAN_WAIT`：从 Preview 锁定到 Human Owner 决策的等待时间，不计为系统计算耗时。

## 4. 价值指标契约

指标必须同时携带公式、分子、分母、单位、观测窗口、源引用、证据类别、状态和限制。
任何零分母、证据类别混合、目标集漂移或成本模型漂移都会失败关闭。

| 指标 | 公式 | 当前状态 |
|---|---|---|
| 报价周期 | `delivery_accepted_at - request_admitted_at` | 真实基线 `NOT_RUN` |
| 政策确认主动工时 | `sum(owner_active_interval_minutes)` | `NOT_RUN` |
| 完整影响结果不匹配 case 率 | 存在任意 impact mismatch 的合成变化 case / 已评估合成变化 case | 仅 `SYNTHETIC_GOLD`；不是 target 漏改率 |
| 首次交付返工率 | 需更正 Quote / 已交付 Quote | `NOT_RUN` |
| 越权访问成功率 | 成功的越权读取 / 越权读取尝试 | 仅 `SYNTHETIC_GOLD` |
| 待审决策率 | `HOLD_FOR_REVIEW` case-target decision / 已评估 case-target decision | `OBSERVED_LOCAL_PRODUCT` 队列压力代理指标 |
| 选择性 Rebase 成本节省 | `(full - selective) / full` | `MODELLED_COUNTERFACTUAL`，必须附声明场景 envelope |

指标注册表位于
[`quote-value-metric-registry.json`](../configs/workspace/quote-value-metric-registry.json)。

## 5. 为什么“1/4 REBUILD”不等于“节省 75%”

一次选择性更新仍然包含保留核验、人工审阅、挂起和 Skill 重认证。QuoteValue 使用组合成本：

- `HOLD_FOR_REVIEW = REVIEW + HOLD`；
- `REQUALIFY = REVIEW + REQUALIFY`；
- `REBUILD = REBUILD`；
- `PRESERVE_WITHIN_BOUNDARY = PRESERVE`。

所有分量都必须为正数。评估器用同一组目标、同一成本模型分别计算“全部 REBUILD”和
“使用已观测 disposition”。基准系数得到 `49.4%` 的模型化节省；LOW/HIGH 两个声明的审阅
负担场景产生 `35.0%–59.9%` 的 envelope，它不是统计置信区间。BREAK_EVEN 与 ADVERSE 压力
场景使四场景 envelope 扩展为 `-10.0%–59.9%`，明确证明“选择性更新一定省成本”并不是当前
结论。这些都是可复算的归一化反事实，不是金额、人天、实际节省或企业 ROI。

分母 `8.00` 来自 `len(effects) * REBUILD`：假设八个目标全部重建，并非观测到的旧流程成本。
`current-process-baseline.json` 仍为 `NOT_RUN`；`REVIEW=0.35` 等系数未经企业实测校准。

## 6. 当前可重放证据

```bash
python scripts/run_quote_value_benchmark.py --output-dir evidence/quote-value/latest
python scripts/verify_quote_value_evidence.py \
  --receipt evidence/quote-value/latest/quote-value-receipt.json
```

评估器与确定性 replay 验证器都不导入 `orgrebase`。二者共享同一个 canonical
`verify_receipt` 实现，因此这里的“product-independent”不等于第二套独立实现。它们只读取：

1. QuoteValue 的流程、指标和成本契约；
2. OWB 的合成金标结果；
3. ProductPath 本地真实 HTTP 观测；
4. 币种变化和发布日变化的两份保留 VMRC。

输出收据会复算 OWB report、ProductPath observation 和 VMRC 当前可用的内容地址；它不重新运行
OWB 或 ProductPath 的源执行。收据将原始 disposition 标记为 `OBSERVED_LOCAL_PRODUCT`，将成本
换算标记为 `MODELLED_COUNTERFACTUAL`，将真实业务基线保留为 `NOT_RUN`。OWB 的 `72/72`
表示 72 个完整 synthetic CHANGE case 的 impact equality，不表示 72 个受影响 target，也不等于
真实漏改率。

## 7. Shadow Pilot 如何将 `NOT_RUN` 变为真实证据

同一家企业、同一个报价队列、同一个观测窗口必须同时收集：

- 请求入场、各领域响应、交付和验收时间戳；
- Product、Legal、Finance 负责人的主动处理时段；
- 首次交付后的更正记录和原因编码；
- 越权读取尝试及最终结果；
- 每个变化目标的 `PRESERVE / REVIEW / HOLD / REQUALIFY / REBUILD` 实际工时；
- 人工升级、改派、补偿、恢复和最终验收。

在这些记录入库前，不使用 Demo 时长替代报价周期，不使用 planner `declared_cost`
替代金钱或人工工时，不对外宣称企业 ROI。
