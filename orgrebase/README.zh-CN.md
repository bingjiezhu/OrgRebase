<p align="right"><a href="README.md">English</a> · <strong>简体中文</strong></p>

# OrgRebase

**治理企业变更：从来源事实到获批成果。**

OrgRebase 协调企业事实、政策、职责和产品方案的变更，使已有工作按确切依据更新。它将变更绑定到来源和负责人，识别受影响的成果，准备可审阅的后继版本，再应用有权负责人批准的精确结果。Agent、Tool 和 Skill 生成候选与证据；确定性控制面约束范围、审批和规范写入。

当前可运行的参考场景是 **Enterprise Quote（企业报价）**，包含 Product、Legal、Finance 和 GTM 领域角色。折扣调整是企业变更的一种示例：系统识别依赖旧值的报价，保留不受影响的工作，并核对获批后继成果。无模型计价首跑使用公开历史商品明细，计价模板浏览器流程使用合成初始事实，原生模型参考旅程使用受控的合成企业 Pack。各路径中的政策与身份均为受控输入。

本源码版本为 **`0.5.0b4` Beta**，验证范围为受控本地执行；客户接入和生产资格须单独验收。

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)
[![文档](https://img.shields.io/badge/docs-source%20guides-0A66C2)](docs/guide/index.zh.md)
[![许可](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## 从这里开始

| 目标 | 文档 |
|---|---|
| 安装并核对一笔交易 | [首次运行](#第一次运行先核对一笔真实商品数据) · [快速开始](docs/guide/quickstart.zh.md) |
| 理解设计 | [核心方法](docs/guide/approach.zh.md) · [架构](docs/guide/architecture.zh.md) |
| 在 WebUI 中审阅和批准变更 | [浏览器操作指南](docs/guide/demo.zh.md) |
| 调整受支持的企业输入 | [复用指南](docs/REUSE-AND-LICENSING.md#try-an-enterprise-pack-without-changing-the-engine) · [Skill 与企业 Pack](docs/guide/skills.zh.md) |
| 集成或运维服务 | [API 接口](docs/MODEL-AGENT-TOOL-INTERFACES.md#public-api-surface) · [部署](docs/guide/deployment.zh.md) |
| 贡献代码或文档 | [开发检查](#开发与反馈) · [贡献指南](CONTRIBUTING.md) |

源码文档与代码一起维护。[线上文档站](https://bingjiezhu.github.io/OrgRebase/)可能对应较早版本。

## 第一次运行：先核对一笔真实商品数据

使用 CPython 3.12–3.14 与 [uv](https://docs.astral.sh/uv/)。从包含 `pyproject.toml`、`uv.lock` 和 `scripts/` 的产品目录执行；工作区 clone 中的路径为 `OrgRebase/orgrebase`。

本地复算会写入 SQLite 数据库。Python 所链接的 SQLite 必须为 3.51.3 或更高版本，或带官方补丁的 3.44.x/3.50.x 分支。使用系统 Python 前先核对[链接库要求与升级方法](docs/guide/quickstart.zh.md#sqlite-runtime)。

```bash
uv sync --locked
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

安装依赖需要网络或完整缓存。复算本身不需要模型、云凭据、PostgreSQL 或安装 OAC。输出目录必须事先不存在，每次运行使用新路径。

打开 `../quote-first-run/index.html`。默认样本的首单为发票 `560602`，应显示 `PASS`。在 `report.json` 中核对 `status=PASS`，以及 `measurement_summary` 下的字段：

```text
planned_cases = 1
outcomes.PASS = 1
outcomes.FAILED = 0
outcomes.INCOMPLETE = 0
```

这条命令执行报价形成、预演、脚本身份批准、应用和独立金额核对，同时检查拒绝路径。商品明细来自公开历史数据；折扣、税率和组织身份为受控输入。这验证本地计价与治理链路。人工审阅操作见[浏览器指南](docs/guide/demo.zh.md)，完整步骤见[快速开始](docs/guide/quickstart.zh.md)。

## 创建计价工作区

从 `orgrebase/` 目录初始化带结构化计价输入的受支持模板：

```bash
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack
```

该模板提供 v2 Quote 适配器所需的合成初始事实和负责人映射；创建或密封不会授权任务或批准企业变更。[无模型浏览器流程](docs/guide/demo.zh.md#model-free-priced-workspace)使用新的 Quote + Discount Memo 工作区，依次完成契约准入、任务确认、来源审阅、两位成果负责人批准和授权执行。示例初始总额为 USD 95.00，10% 折扣获批生效后为 USD 90.00。输出目录和数据库均须新建。

## 支持范围

| 企业变更或能力 | 已实现的参考范围 |
|---|---|
| 上线日期、币种、产品方案 | 在企业报价参考场景中绑定来源，核对依赖并由负责人审阅 |
| 报价商品与计价政策 | 显式 v2 计价配置，包含确定性金额计算与独立核验 |
| 职责移交 | 显式启用、双方负责人确认的策略 |
| Quote 与 Discount Memo | 在新建隔离工作区中启用的显式双成果配置 |
| 经验与 Skill 演化 | 受治理的候选和资格验证切片；跨任务质量、完整发布与采用仍开放 |

新成果类型需要已准入的处理器、类型化来源、负责人和验收用例。真实企业身份、来源与目标系统接入须分别取得资格。现有 Dataverse 目标适配器只更新已配置报价草稿的 `name` 和 `description`，不写入价格或行项目。精确边界见[系统地图](docs/SYSTEM-MAP.md)、[计价报价指南](docs/PRICED-QUOTE-DEMO.md)和[目标运维](docs/DATAVERSE-TARGET-OPERATIONS.md)。

要验证受支持的配置复用，可以[创建并密封企业 Pack](docs/REUSE-AND-LICENSING.md#try-an-enterprise-pack-without-changing-the-engine)，调整 `product_plan` 与 `currency` 事实。这项练习使用现有处理器，不修改引擎。

## 架构概览

| 组件 | 职责 |
|---|---|
| OAC | 约定事实、负责人、能力和准入范围的组织契约草案 |
| AgentTeams | 任务执行、受限上下文和证据交接 |
| Agent、Tool 和 Skill | 生成候选与收集证据 |
| OrgRebase 控制面 | 来源与依赖检查、影响分析、预演、权限核对和恢复 |
| 负责人和授权执行者 | 审阅精确候选，通过规范写入路径应用获批变更 |

选择性 Rebase 更新受影响的成果，在已核验的依赖边界内保留不受影响的工作。覆盖不足保持 `UNKNOWN`。执行完成、候选准入、独立接受、人工批准和应用是分别记录的状态。批准本身不修改业务状态；StateStore 和 RebaseWorkflow 持有规范写入职责。

[核心方法](docs/guide/approach.zh.md)说明设计依据；[架构指南](docs/guide/architecture.zh.md)和[补证恢复](docs/guide/agentteams.zh.md)说明实现与恢复行为。

## 运行参考主线

公开工作区包含并列的 `orgrebase/` 与 `oac-spec/` 项目。使用模型的参考旅程需要匹配的 OAC 源码、有权使用的 Vertex 项目，以及保存在仓库外的凭据。从产品目录运行：

```bash
export ORGREBASE_VERTEX_PROJECT="YOUR_GCP_PROJECT"
export ORGREBASE_VERTEX_MODEL_ID="gemini-3.8-flash"
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
./run-semifinal-demo.sh live
```

显式 `live` 启动模式默认使用 Vertex Gemini 3.8 Flash。云调用可能产生服务商费用，配置步骤见 [Vertex 指南](docs/guide/models-vertex.zh.md)，页面操作见[浏览器指南](docs/guide/demo.zh.md)。参考场景中的 AgentTeams 执行与独立契约核验运行在本地，Matrix 和对象存储传输使用受控夹具；外部分布式执行须另行验证。

可选模型路径与历史证据核验见[模型接口](docs/MODEL-AGENT-TOOL-INTERFACES.md)。[部署指南](docs/guide/deployment.zh.md)覆盖认证 PostgreSQL 运行、来源绑定、目标资格和运维前置条件。

## 开发与反馈

安装开发工具，运行有明确范围的核心检查：

```bash
uv sync --locked --extra dev
make check-core
```

`make check-core` 从随附源码包重建固定版本的 AgentTeams checkout，不需要 OAC、PostgreSQL 或服务凭据。开发工作区的完整 `make check` 还依赖匹配的 OAC 项目、PostgreSQL 工具，以及公开源码范围不包含的历史档案。公开目录支持的检查见[贡献指南](CONTRIBUTING.md)和[公开发布检查](docs/RELEASE-CANDIDATES.md)。

通过 [Issues](https://github.com/bingjiezhu/OrgRebase/issues)提交缺陷、功能提案和可复现的复用尝试。漏洞按[安全说明](SECURITY.md)私密报告。[社区指南](docs/COMMUNITY.md)列出公开参与入口。

## 仓库地图

```text
src/orgrebase/                    运行程序与确定性控制面
examples/enterprise-quote-pilot/   有明确边界的企业参考 Pack
skills/                          受治理 Skill 包
schemas/                         契约与投影
tests/                           行为与边界检查
docs/guide/                      用户与贡献者文档
vendor/agentteams/                固定版本、可重建的源码包
```

`uv.lock` 记录依赖解析。OAC 在相邻的 `oac-spec/` 项目中维护，不包含在 OrgRebase Python 包内。

## 项目状态

本 Beta 对明确列出的参考路径具有受控本地验证。客户 IAM 接入、员工 UAT、生产容量、SLA 和 ROI 须单独验收。验证范围与发布检查见[发布资格说明](docs/RELEASE-CANDIDATES.md)。

## 许可

OrgRebase 的项目自有材料适用 **[Apache-2.0](LICENSE)**。

第三方代码、依赖、数据集和可选模型保留各自条款与来源通知。详见 [许可范围](LICENSE.md)、[第三方清单](docs/THIRD-PARTY-INVENTORY.md)和 [NOTICE.md](NOTICE.md)。
