# OrgRebase 文档

本目录对应 OrgRebase `0.5.0b4` Beta 候选。OrgRebase 处理企业变更后的工作更新：先确定事实、政策、职责与目标，再准备候选、核对依赖与影响、交给有权负责人批准，最后由授权主体写入后继结果。当前可运行参考场景是企业报价；[核心方法](guide/approach.zh.md)从一次折扣调整讲清这条链，也列出当前支持的其他变化类型。产品范围和权威边界以 [SYSTEM-MAP](SYSTEM-MAP.md) 为准。

已部署的 [GitHub Pages](https://bingjiezhu.github.io/OrgRebase/) 可能对应较早提交；判断当前版本请先读仓库内同版 Markdown 与站点“本站源码与下载”页。

## 第一次使用

| 你想做什么 | 从这里开始 |
|---|---|
| 先理解企业变更、设计与边界 | [核心方法](guide/approach.zh.md) / [Core approach](guide/approach.en.md) |
| 安装并运行一单公开交易报价 | [中文快速开始](../README.zh-CN.md#第一次运行先核对一笔真实商品数据) / [English quickstart](../README.md#first-run-verify-one-public-transaction)；无需模型或 OAC |
| 在浏览器中修改折扣、批准并看到金额变化 | [公开数据计价演示](PRICED-QUOTE-DEMO.md) |
| 了解日期、币种和产品方案变化的运行合同 | [用户与应用场景](USER-AND-APPLICATION-SCENARIO.md)、[来源恢复](SOURCE-READMISSION.md) |
| 运行包含 OAC 准入、AgentTeams 和批准的参考旅程 | [Enterprise Pilot Runbook](ENTERPRISE-PILOT-RUNBOOK.md)；先核对 OAC、模型与本地/客户证据边界 |
| 看一次连续 Demo 的操作顺序 | [Demo Runbook](DEMO-RUNBOOK.md) |
| 配置自己的企业包，了解可复用部分与适配条件 | [复用与许可指南](REUSE-AND-LICENSING.md) |
| 核对运行结果、测试范围与资格 | [验证方法](WORKSPACE-EVALUATION.md)、[公开发行检查](RELEASE-CANDIDATES.md) |

## 理解系统和接口

| 主题 | 文档 |
|---|---|
| 用户、现实流程、职责与完成条件 | [企业报价运营模型](ENTERPRISE-QUOTE-OPERATING-MODEL.md) |
| 新建 Quote + Discount Memo 双成果工作区 | [双成果运行合同与验证](QUOTE-DISCOUNT-MEMO.md) |
| 模块、数据流与权威分工 | [架构说明](ARCHITECTURE.md)、[系统架构图](diagrams/orgrebase-system-architecture.html) |
| Apply 的校验、事务、幂等与恢复顺序 | [Apply 事务顺序](APPLY-TRANSACTION.md) |
| OAC 组织契约与业务 Runtime 的关系 | [OAC / OrgRebase 边界](OAC-ORGREBASE-PRODUCT-BOUNDARY.md) |
| 模型、Agent 与工具接口 | [接口说明](MODEL-AGENT-TOOL-INTERFACES.md)、[Tool 契约](TOOL-CONTRACT.md) |
| Skill 的发现、调用、版本和治理 | [Skill 清单](SKILL-LIST.md)、[Quote 补证经验的受控演化](GOVERNED-QUOTE-RECOVERY-LEARNING.md) |
| WebUI 和 Element 如何查看同一任务 | [Matrix / Element 接入](MATRIX-OBSERVATION.md) |
| 参考工作流与运行步骤 | [Workspace 参考运行](WORKSPACE-DEMO-RUNBOOK.md) |

## 接入、部署和运维

| 主题 | 文档 |
|---|---|
| 启动命令、身份、权限与 PostgreSQL | [运行命令](OPERATOR-COMMANDS.md)、[认证部署](AUTHENTICATED-DEPLOYMENT.md) |
| 真实来源、字段映射与负责人确认 | [Dataverse 来源接入](DATAVERSE-SOURCE-ONBOARDING.md)、[来源连接器](SOURCE-CONNECTOR.md) |
| 企业五类输入、Pack 密封、准入和激活状态 | [企业接入状态](ENTERPRISE-ONBOARDING.md) |
| 外部目标写入、回查和不确定效果 | [Dataverse 目标操作](DATAVERSE-TARGET-OPERATIONS.md) |
| 来源变化后的重新准入 | [多来源恢复](SOURCE-READMISSION.md) |
| 变化待办、后台候选准备和接续边界 | [变化运营](CHANGE-OPERATIONS.md) |
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
| 版本变更、制品身份和交付核对 | [CHANGELOG](../CHANGELOG.md)、[公开制品资格与撤回](RELEASE-CANDIDATES.md) |
| 报告漏洞与参与项目 | [安全报告](../SECURITY.md)、[行为准则](../CODE_OF_CONDUCT.md)、[Issues](https://github.com/bingjiezhu/OrgRebase/issues) |
| 验证覆盖范围与复现方式 | [评测方法与局限](WORKSPACE-EVALUATION.md)、[贡献者检查](../CONTRIBUTING.md) |

每个运行结果由其 manifest、来源摘要和校验器标识。公开源码检查、安装包测试与历史回执分别记录适用范围；保留的 `release-facts` 通过 `release_context` 标记历史版本，不能证明新制品通过验证。客户身份、员工 UAT、生产容量及业务价值需要独立验收。

- [主项目与公开迭代同步](RELEASE-SOURCE-PARITY.md)：固定白名单派生、逐文件核对与历史事实边界。
