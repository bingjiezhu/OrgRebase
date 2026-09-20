# OrgRebase 文档

OrgRebase 处理规则变化后的工作更新：生成候选、核对影响、交给有权负责人批准，再写入后继结果。
当前可运行参考场景是企业报价。产品范围和权威边界统一见 [SYSTEM-MAP](SYSTEM-MAP.md)，
各专题说明具体接口、操作和验证方法。

## 第一次使用

| 你想做什么 | 从这里开始 |
|---|---|
| 安装并运行一单公开交易报价 | [中文快速开始](../README.zh-CN.md#第一次运行先核对一笔真实商品数据) / [English quickstart](../README.md#first-run-verify-one-public-transaction)；无需模型或 OAC |
| 在浏览器中修改折扣、批准并看到金额变化 | [公开数据计价演示](PRICED-QUOTE-DEMO.md) |
| 核对日期、币种和产品变化的多领域链路与数据验证范围 | [上游变化与数据验证复核](UPSTREAM-CHANGE-REVIEW.md) |
| 运行包含 OAC 准入、AgentTeams 和人工批准的完整旅程 | [Enterprise Pilot Runbook](ENTERPRISE-PILOT-RUNBOOK.md)；先核对 OAC 与模型依赖 |
| 看一次连续 Demo 的操作顺序 | [Demo Runbook](DEMO-RUNBOOK.md) |
| 换成自己的规则包，了解哪些可复用、哪些必须适配 | [复用与许可指南](REUSE-AND-LICENSING.md) |
| 独立核对已保留的运行证据 | [验证证据图](VERIFICATION-EVIDENCE-MAP.md)；历史核验与新执行分别记录 |

## 理解系统和接口

| 主题 | 文档 |
|---|---|
| 用户、现实流程、职责与完成条件 | [企业报价运营模型](ENTERPRISE-QUOTE-OPERATING-MODEL.md) |
| 模块、数据流与权威分工 | [架构说明](ARCHITECTURE.md)、[系统架构图](diagrams/orgrebase-system-architecture.html) |
| Apply 的校验、事务、幂等与恢复顺序 | [Apply 事务顺序](APPLY-TRANSACTION.md) |
| OAC 组织契约与业务 Runtime 的关系 | [OAC / OrgRebase 边界](OAC-ORGREBASE-PRODUCT-BOUNDARY.md) |
| 模型、Agent 与工具接口 | [接口说明](MODEL-AGENT-TOOL-INTERFACES.md)、[Tool 契约](TOOL-CONTRACT.md) |
| Skill 的发现、调用、版本和治理 | [Skill 清单](SKILL-LIST.md) |
| WebUI 和 Element 如何查看同一任务 | [Matrix / Element 接入](MATRIX-OBSERVATION.md) |
| 参考场景与历史演化 | [Workspace 参考运行](WORKSPACE-DEMO-RUNBOOK.md)、[初赛至 OAC 架构](PRELIMINARY-TO-OAC-EVOLUTION-ARCHITECTURE.md) |

## 接入、部署和运维

| 主题 | 文档 |
|---|---|
| 启动命令、身份、权限与 PostgreSQL | [运行命令](OPERATOR-COMMANDS.md)、[认证部署](AUTHENTICATED-DEPLOYMENT.md) |
| 真实来源、字段映射与负责人确认 | [Dataverse 来源接入](DATAVERSE-SOURCE-ONBOARDING.md)、[来源连接器](SOURCE-CONNECTOR.md) |
| 外部目标写入、回查和不确定效果 | [Dataverse 目标操作](DATAVERSE-TARGET-OPERATIONS.md) |
| 来源变化后的重新准入 | [多来源恢复](SOURCE-READMISSION.md) |
| 数据库升级、备份与恢复 | [数据库迁移](STATE-STORE-MIGRATIONS.md) |
| 日志、错误定位、观察和退出 | [运维与数据退出](OPERATIONS-AND-EXIT.md) |
| 数据边界、留存与删除 | [数据与隐私](WORKSPACE-DATA-AND-PRIVACY.md)、[私有数据生命周期](PRIVATE-DATA-LIFECYCLE.md) |
| 测容量与过载恢复 | [HTTP 容量观察](HTTP-CAPACITY.md) |
| 测业务效果及成本 | [配对业务观测](BUSINESS-OBSERVATIONS.md) |

## 贡献、发行与验证

| 主题 | 文档 |
|---|---|
| 从基础检查开始贡献代码 | [贡献指南](../CONTRIBUTING.md)；`make check-core` 与完整 `make check` 的范围不同 |
| 开放范围、商业使用和第三方依赖 | [复用与许可](REUSE-AND-LICENSING.md)、[依赖清单](THIRD-PARTY-INVENTORY.md)、[NOTICE](../NOTICE.md) |
| 版本变更、制品身份和交付核对 | [CHANGELOG](../CHANGELOG.md)、[制品资格与撤回](RELEASE-CANDIDATES.md)、[历史发行验证（2026-09-02）](../RELEASE-VERIFICATION.md)；本次或后续候选须运行所绑定工作区的 `make check-core`、满足 OAC/PostgreSQL 前置条件后的完整 `make check`，并核对发行包随附清单与验证回执 |
| 报告漏洞与参与项目 | [安全报告](../SECURITY.md)、[行为准则](../CODE_OF_CONDUCT.md)、[Issues](https://github.com/bingjiezhu/OrgRebase/issues) |
| 验证覆盖范围与复现方式 | [证据与复现命令](VERIFICATION-EVIDENCE-MAP.md)、[评测方法与局限](WORKSPACE-EVALUATION.md) |

历史运行由对应 manifest、来源摘要与独立校验器标识。`evidence/release-facts.json` 描述早期
参考主线，`evidence/golden-competition/latest/pilot/` 保留其确切 Golden；两者都不自动证明
后来的源码、现场运行或客户部署。临时计划、创作素材和审查过程记录保存在可运行源码之外，
运行与发行不依赖这些材料。
