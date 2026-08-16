<p align="right"><a href="README.md">English</a> · <strong>简体中文</strong></p>

# OrgRebase Workspace

**知变：企业工作持续演化引擎。让企业交付物记录自己依赖的组织事实；事实变化后，系统定位失效部分，给出可验证的最小重建计划，并生成带证据的新版本。**

**许可证。** 源码可用双许可：[非商业 PolyForm Noncommercial 1.0.0](LICENSE) · [商业使用须单独授权](COMMERCIAL-LICENSE.md)。本项目不宣称为 OSI Open Source。

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)

<p align="center">
  <a href="#使用场景">使用场景</a> ·
  <a href="#核心架构">架构</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#验证材料">验证</a> ·
  <a href="#许可证">许可证</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

## 使用场景

销售或 Deal Desk 为客户生成企业报价时，需要同时确认 Product、Legal、Finance、GTM 四个权威域的信息。常见流程依赖人工询问和复制，报价很难说明使用了哪些来源与版本；发布日期、币种或政策变化后，也缺少可靠的方法找回并更新受影响的历史工作。

OrgRebase 把一次报价视为可追踪的构建过程：

```text
员工任务 + 已准入的组织前提 + 受治理 Skill
    -> 版本化 Quote + WorkTrace + RuntimeDependencyManifest

组织前提变化
    -> ImpactCertificate + 精确 VMRC
    -> 选择性重建 Quote + 后继依赖图 + RebaseReceipt
```

当前仓库提供可执行的合成企业切片。目标用户、人工流程、部署路径和外部验证协议见 [USER-AND-APPLICATION-SCENARIO](docs/USER-AND-APPLICATION-SCENARIO.md)。

## 核心架构

### Agent 架构

Workspace 使用固定的 AgentTeams Worker 池，再为每个任务选择最小联盟。它不让一个 Agent 读取整家公司，也不让 Agent 通过自由对话取得权威。

| 角色 | 职责 | 权限边界 |
|---|---|---|
| Employee Task Agent | 把员工任务约束到已发布 `TaskTemplate` 和声明槽位 | 不是 AgentTeams Worker；不能发明槽位、选定权威或写状态 |
| Product Steward | 产品套餐、发布日期、数据驻留候选 | 不能代表 Legal/Finance/GTM 或准入自身候选 |
| Legal Steward | 从受限来源派生最小通知义务 | 原始合同文本不能离开 Legal 域 |
| Finance Steward | 价格区间与币种政策候选 | 不能读取 Legal 来源或批准变更 |
| GTM Steward | 客户、任务和交付物语义 | 不能标记 Work 状态或决定影响 |

`CoalitionPlanner` 在四张能力卡的 15 个非空子集中按覆盖、数量、成本和字典序选择。AgentTeams 负责角色承载、结构化任务传输和运行状态；确定性控制面负责权威、准入、上下文、影响、审批和状态。

项目还有一条变化咨询协作面：`change-coordinator -> product/finance -> gtm`。Impact 与 VMRC 在咨询前已经锁定，咨询结果只作解释，`target_writes=0`。Core 五 Agent 的冻结 live receipt 属于另一条 change-advisory 切片，不能代替 Workspace formation live。

完整 Identity 八字段和 AgentTeams 能力映射见 [AGENT-IDENTITIES](docs/AGENT-IDENTITIES.md) 与 [WORKSPACE-ARCHITECTURE](docs/WORKSPACE-ARCHITECTURE.md)。

### Skill 工程层

| Skill | 作用 | 当前状态 |
|---|---|---|
| `structured-domain-handoff@1.0.0` | 在 AgentTeams Worker 与控制面之间传输精确、零写入的 Domain 候选 | 静态/本地合约通过；Workspace live `NOT_RUN` |
| `enterprise-quote-compose@1.1` | 把报价经验封装成三种允许操作的声明式候选程序 | 由独立 Evaluator 运行 exact bytes；本地评测为 `CANARY` |
| `enterprise-launch-readiness@1.3` | 将 Core ImpactResult 映射为受限行动候选 | Core 本地资格评测、隔离与回滚通过 |

Agent 判断任务与协作，Skill 封装可复用能力，工具负责外部访问，控制面保留发布和写入权。Skill 候选不能读取 held-out 答案、修改评测器、扩大权限、发布自己或执行任意 Python。完整输入输出、触发、依赖、失败、安全和复用字段见 [SKILL-LIST](docs/SKILL-LIST.md)。

### 依赖来自执行

Quote renderer 只能调用 `ExecutionReferenceMonitor.resolve(slot_id)`。每次读取都进入摘要链，输出字段再绑定到已读取槽位或声明的任务常量。系统据此生成 `WorkTrace` 与 manifest；依赖不是 Agent 自报，也不是“曾出现在 Prompt 里”的推断。

### 可验证的选择性重建

完整 manifest 缺失时，系统不能给出“未受影响”结论；证据不足返回 `UNKNOWN`。VMRC 对精确 effect set 做双向检查：少一个必要 `REBUILD` 失败，多一个无关 `REBUILD` 也失败。审批绑定 ChangeSet、Preview、VMRC、授权根、范围和有效期；Apply 在事务前重新校验全部绑定。

### Skill 与组织经验一起演化

Curator 只生成不可变声明式候选。Evaluator 使用同一组 replay、held-out、negative-transfer、permission、injection、malformed、resource 和 canary 分区比较 no-Skill、previous-Skill 与 candidate。安全失败直接隔离候选；发布结果只有 `CANARY` 或 `QUARANTINED`。

## 端到端闭环

1. 接收 `TaskRequest`。
2. Task Agent 匹配模板并提出声明槽位。
3. Planner 选择 Product/Legal/Finance/GTM 最小联盟。
4. AgentTeams/local transport 传递 actor-specific projection 和候选字节。
5. 控制面执行 authority、purpose、recipient、organization、task scope、freshness 准入。
6. Reference Monitor 形成 Quote v1、WorkTrace、manifest 和 graph v1。
7. 只读依赖工具产生 `ToolInvocationReceipt` 与同 run 的 `ToolCalledEvent`。
8. 发布日期变化生成 Preview、ImpactCertificates、VMRC、审批与 Quote v2。
9. 进程重启后从 SQLite 指针恢复状态，币种变化生成 Quote v3 与 graph v3。
10. 轨迹沉淀为 Skill candidate，独立评测后进入 CANARY 或隔离。
11. Evidence Index 绑定任务、Agent、工具、Skill、审批、状态和文件摘要。

异常、冲突、超时、越权、漂移、幂等冲突、事务回滚和 live 证据拒绝路径见 [AGENT-TASK-CLOSURE](docs/AGENT-TASK-CLOSURE.md)。

## Tool、MCP、上下文与可观测

- 无 MCP Server。`ToolContract` 提供协议、鉴权、Schema、错误、重试、幂等、审计、降级和 MCP 迁移字段；后续迁移只增加协议适配层。
- 不使用知识库 RAG。赛题四项上下文能力中已实现两项：SQLite 共享状态和 WorkTrace/事件/OTLP 轨迹可观测。
- Core 导出 OTLP/JSON Trace、Log、Metrics；Workspace 使用 WorkTrace、工具事件、SQLite 摘要链和 Evidence Index。
- 推荐工具的版本、替代原因、接口、权限边界和迁移工作量见 [THIRD-PARTY-INVENTORY](docs/THIRD-PARTY-INVENTORY.md)。

## 快速开始

需要 Python `>=3.12,<3.15`。推荐使用 `uv`：

```bash
git clone https://github.com/bingjiezhu/OrgRebase.git
cd OrgRebase
uv sync --all-extras
make workspace-check
make workspace-review-readiness-check
make workspace-demo
```

没有 `uv` 时：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m orgrebase workspace-demo --output-dir evidence/workspace/latest
```

预期业务摘要：

```text
OrgRebase Workspace: PASS
Final Quote: work:quote_acme@v3 · 2026-09-15 · EUR
Final Graph: graph:workspace@v3
OWB v1.1: PASS
Skill: CANARY
Workspace AgentTeams live: NOT_RUN
```

启动 API：

```bash
make serve
```

主要接口：

```text
POST /api/demo/workspace/quote-to-rebase
GET  /api/workspace/state
POST /api/workspace/form
POST /api/workspace/preview/{launch_date|currency}
POST /api/workspace/apply/{launch_date|currency}
POST /api/workspace/run
GET  /api/demo/workspace/agentteams-status
GET  /api/tools/v1/dependency-evidence/contract
POST /api/tools/v1/dependency-evidence
```

## 验证材料

| 内容 | 位置 |
|---|---|
| 输入、联盟与 Quote v1 | `examples/`、`evidence/workspace/latest/formation/` |
| 两次变化、审批、VMRC 与 Quote v2/v3 | `evidence/workspace/latest/rebase/` |
| 重启恢复 | `evidence/workspace/latest/repeatability/` |
| Tool receipt 与 event | `evidence/workspace/latest/tool/` |
| OWB、Skill 与安全评测 | `evidence/workspace/latest/evaluation/`、`skill/` |
| 内容寻址索引 | `evidence/workspace/latest/evidence-index.json` |
| Core ProofPack、OTLP、rollback 与真实本地 Git receipt | `evidence/latest/` |
| 当前机器事实 | `evidence/release-facts.json` |
| 测试、覆盖率与发行包校验 | `RELEASE-VERIFICATION.md` |

赛题逐项映射见 [COMPETITION-TECHNICAL-COMPLIANCE](docs/COMPETITION-TECHNICAL-COMPLIANCE.md)。

## 当前边界

| 项目 | 状态 |
|---|---|
| Workspace 本地确定性任务形成与两次变化 | `PASS` |
| AgentTeams 静态资产与 transport verifier | `PASS` |
| Workspace-specific AgentTeams live | `NOT_RUN` |
| 真实用户验证 | `NOT_RUN`（0 participants） |
| 真实企业连接器与生产 ROI | `NOT_RUN` / 不主张 |

合成 benchmark 证明声明边界内的 conformance，不代表任意企业任务准确率、真实图全局完备性或生产收益。公开包不包含 raw AgentTeams sessions、凭据、Prompt、模型原始输出或 nonce ledger。

## 目录

```text
src/orgrebase/workspace/   Workspace 任务形成、恢复、Skill 与评测
src/orgrebase/             Core 影响、证书、事务、工具与补偿
agentteams/                 Team/Worker/Identity/Skill 与 transport assets
benchmark/orgworkbench/     12 个合成组织与 192 个 conformance cases
configs/workspace/          模型、指标与评审配置
schemas/                    运行模型导出的 JSON Schema
tests/                      正向、负向、攻击、故障与回归测试
evidence/                   内容寻址的运行证据
docs/                       架构、合规、工具、观测、隐私与运行说明
```

## 许可证

项目自有代码、文档、Skills、fixtures 和合成 benchmark 采用 [PolyForm Noncommercial 1.0.0](LICENSE)。商业使用必须取得单独书面授权，见 [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)。本项目属于 source-available，不宣称为 OSI Open Source；第三方依赖保持其原许可证。
