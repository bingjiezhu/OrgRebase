<p align="right"><a href="README.md">English</a> · <strong>简体中文</strong></p>

# OrgRebase

**治理企业变更：从来源事实到获批成果。**

OrgRebase 协调企业事实、政策、职责和产品方案的变更，使已有工作按确切依据更新。它将变更绑定到来源和负责人，识别受影响的成果，准备可审阅的后继版本，再应用有权负责人批准的精确结果。Agent、Tool 和 Skill 生成候选与证据；确定性控制面约束范围、审批和规范写入。

当前可运行的参考场景是 **Enterprise Quote（企业报价）**，包含 Product、Legal、Finance 和 GTM 领域角色。折扣调整是企业变更的一种示例：系统识别依赖旧值的报价，保留不受影响的工作，并核对获批后继成果。无模型计价首跑使用公开历史商品明细，计价模板浏览器流程使用合成初始事实，原生模型参考旅程使用受控的合成企业 Pack。各路径中的政策与身份均为受控输入。

本源码包含 OrgRebase **`0.5.0b4` Beta** 与 OAC **`0.3.0a0`**。验证覆盖有明确边界的本地参考路径；客户接入与生产资格须单独验收。

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)
[![文档](https://img.shields.io/badge/docs-source%20guides-0A66C2)](orgrebase/docs/guide/index.zh.md)
[![许可](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## 快速开始

在仓库根目录，使用 CPython 3.12–3.14 与 [uv](https://docs.astral.sh/uv/)。安装锁定依赖需要网络或完整缓存；下面的复算不需要模型、云凭据、PostgreSQL 或 OAC CLI。

复算会写入本地 SQLite 数据库。Python 所链接的 SQLite 必须为 3.51.3 或更高版本，或带官方补丁的 3.44.x/3.50.x 分支；详见[链接库检查与升级方法](orgrebase/docs/guide/quickstart.zh.md#sqlite-runtime)。

```bash
cd orgrebase
uv sync --locked
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

输出目录必须事先不存在。打开 `../quote-first-run/index.html`，发票 `560602` 应显示 `PASS`。`report.json` 的 `status` 应为 `PASS`，`measurement_summary` 中应有 `planned_cases=1`、`outcomes.PASS=1`、`outcomes.FAILED=0` 和 `outcomes.INCOMPLETE=0`。复算执行报价形成、预演、脚本身份批准、应用与独立金额核对。完整步骤见[快速开始指南](orgrebase/docs/guide/quickstart.zh.md)。

## 创建计价工作区

从 `orgrebase/` 目录初始化带结构化计价输入的受支持模板：

```bash
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack
```

该模板提供 v2 Quote 适配器所需的合成初始事实和负责人映射；创建或密封不会授权任务或批准企业变更。[无模型浏览器流程](orgrebase/docs/guide/demo.zh.md#model-free-priced-workspace)使用新的 Quote + Discount Memo 工作区，依次完成契约准入、任务确认、来源审阅、两位成果负责人批准和授权执行。示例初始总额为 USD 95.00，10% 折扣获批生效后为 USD 90.00。输出目录和数据库均须新建。

## 工作方式

| 边界 | 职责 |
|---|---|
| [OAC](oac-spec/README.zh-CN.md) | 约定事实、负责人、能力与准入范围的组织契约草案 |
| AgentTeams、Agent、Tool、Skill | 受限任务、交接、候选与证据 |
| OrgRebase 控制面 | 来源与依赖检查、影响裁决、精确预演、准入与恢复 |
| Human Owner 与授权执行者 | 批准精确变更，再通过规范写入路径应用 |

控制面依据依赖和实际读取记录保留不受影响的成果；覆盖不足保持 `UNKNOWN`。任务完成或模型输出都不会自动取得业务写入权。每个获批后继成果保留变更、责任人、版本与回执。[核心方法](orgrebase/docs/guide/approach.zh.md)说明设计及其边界。

## 当前参考范围

| 企业变更 | 当前参考行为 |
|---|---|
| 上线日期、币种、产品方案 | 在企业报价参考场景中绑定来源版本，识别影响并交由负责人审阅 |
| 报价商品与计价政策 | 显式 v2 计价配置；金额核对限于受控本地，不推断客户税务或汇率规则 |
| 职责移交 | 显式启用、双方负责人确认的策略 |
| Quote + Discount Memo | 在隔离工作区显式配置的双成果路径；新成果类型仍需准入的处理器 |
| 经验与 Skill 演化 | 受治理的候选和资格验证切片；跨任务质量、完整发布与采用仍开放 |

这些路径有受控本地验证。新成果类型和真实企业接入需要各自的准入与验收。[企业 Pack 练习](orgrebase/docs/REUSE-AND-LICENSING.md#try-an-enterprise-pack-without-changing-the-engine)以 `product_plan` 和 `currency` 事实为例，说明如何调整企业输入而不修改引擎。

## 文档与参与

| 目标 | 入口 |
|---|---|
| 理解项目与权威边界 | [核心方法](orgrebase/docs/guide/approach.zh.md) · [架构](orgrebase/docs/guide/architecture.zh.md) |
| 运行报价场景或查看 WebUI | [快速开始](orgrebase/docs/guide/quickstart.zh.md) · [演示指南](orgrebase/docs/guide/demo.zh.md) |
| 复用控制面、Skill 或企业 Pack | [复用指南](orgrebase/docs/REUSE-AND-LICENSING.md) · [Skill](orgrebase/docs/guide/skills.zh.md) |
| 配置模型或认证部署 | [Vertex](orgrebase/docs/guide/models-vertex.zh.md) · [部署](orgrebase/docs/guide/deployment.zh.md) |
| 浏览同版文档 | [中文](orgrebase/docs/guide/index.zh.md) · [English](orgrebase/docs/guide/index.en.md) · [线上站点](https://bingjiezhu.github.io/OrgRebase/)（可能落后于源码） |
| 贡献或私密报告漏洞 | [贡献指南](CONTRIBUTING.md) · [社区](COMMUNITY.md) · [安全](SECURITY.md) |

仓库中的 `orgrebase/` 包含运行程序、WebUI、Skill、测试和文档；`oac-spec/` 包含契约、编译器、验证器和一致性测试。Python 包在 `orgrebase/` 内，OAC 作为并列项目维护。[产品 README](orgrebase/README.zh-CN.md)给出原生 AgentTeams 旅程与开发检查。

## 项目状态

本 Beta 对明确列出的参考路径具有**受控本地验证**。公开数据复算、本地 PostgreSQL 与协议夹具检查证明各自环境中实际执行的行为。客户身份接入、员工 UAT、生产容量、SLA 和 ROI 须单独验收。验证范围与发布检查见[发布资格说明](orgrebase/docs/RELEASE-CANDIDATES.md)。

每个 PR 和 main 推送均运行产品、OAC 与企业边界 CI；发布资格检查由维护者单独启动。使用某个版本时，应核对其工作流结果。[线上 Pages](https://bingjiezhu.github.io/OrgRebase/)可能对应较早提交。

## 许可

OrgRebase 与 OAC 的项目自有材料适用 **[Apache-2.0](LICENSE)**。

第三方代码、依赖、数据集和可选模型保留各自条款与来源通知。组件许可及归属见 [LICENSES.md](LICENSES.md)。
