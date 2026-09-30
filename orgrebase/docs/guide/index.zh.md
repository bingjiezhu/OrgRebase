# OrgRebase 文档

OrgRebase 通过组织契约、候选校验、精确审批和选择性 Rebase，管理企业变更后的工作更新。变更可以来自事实、政策、职责或产品方案；系统识别受影响的任务和成果，保留有依据的不变部分，并将正式生效绑定到负责人批准的确切版本。

本版文档对应 **OrgRebase `0.5.0b4` Beta**。企业报价是当前可运行的参考场景；默认单 Quote 与显式 Quote＋Discount Memo 双成果配置有各自的受控验证范围。OAC 提供组织契约草案，AgentTeams 记录任务执行，Skill 提供受约束的能力。[核心方法](approach.zh.md)说明这些组件怎样共同处理一次企业变更。

## 从哪里开始

| 目标 | 入口 |
|---|---|
| 理解企业变更的处理方法 | [核心方法](approach.zh.md) |
| 先确认安装和计价闭环 | [快速开始](quickstart.zh.md)，不需要模型或客户凭据 |
| 配置云模型参考旅程 | [Vertex](models-vertex.zh.md)；[DeepSeek](models-deepseek.zh.md)是可选 Reviewer 路径，证据等级不同 |
| 观察批准前后的业务结果 | [Demo与验证](demo.zh.md) |
| 理解四层权威和状态更新 | [架构](architecture.zh.md)、[AgentTeams](agentteams.zh.md) |
| 适配企业事实与规则、规划接入 | [Skill与复用](skills.zh.md)、[部署与运维](deployment.zh.md) |
| 贡献、发布或商业采用 | [贡献](contributing.zh.md)、[发布](publishing.zh.md)、[许可](licensing.zh.md) |

## 能证明什么

候选、复核、批准和生效是不同事实。公开交易首跑和本地 PostgreSQL/OAC 检查证明各自限定范围内的行为；它们不能直接证明客户已部署、生产容量或付费价值。公开历史商品来自真实数据，演示折扣、税率和组织身份为受控配置。持续 Skill 学习、真实客户适配与业务收益仍有开放验收项。

核心指南有逐页对应的简体中文和英文。页首语言菜单会切换当前页；[深层技术资料](reference.zh.md)保留原文，并单独标明语言。导航中的“本站源码与下载”标出实际建站字节；线上站点在新版本发布前仍可能是旧快照，以同版源码中的文档为准。
