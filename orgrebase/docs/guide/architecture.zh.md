# 架构与权威边界

## 一条业务链，四层决定权

| 层 | 职责 | 不拥有 |
|---|---|---|
| AgentTeams任务与候选 | 任务创建、委派、交接和执行终态，绑定候选来源 | 业务准入与批准 |
| 确定性控制面准入 | 检查来源、范围、依赖、权限和候选完整性 | 代替负责人作高风险业务决定 |
| 精确Human Owner批准 | 审阅本职责范围内的特定摘要与版本 | 批准其他人或陈旧对象 |
| StateStore / RebaseWorkflow规范写入 | 在事务内产生后继对象、图、回执和指针 | 信任未准入候选 |

这不是四个必须分开的服务。`AT completed`、`Candidate admitted`、`Reviewer accepted`、`Human approved`与`Canonical applied`不能互相替代。

## OAC与团队形成

OAC提供组织、知识、权限、能力与交接的契约及验证语义。当前Quote路径由OrgRebase控制面形成团队并编译AT执行计划；Quote兼容性回执不冒充OAC OrganizationPlan或PlanCertificate。跨任务可以重新组队；已准入计划不就地改写，补证恢复另建执行轮次并保留旧计划、输入、候选与回执。

## 选择性Rebase

实际读取与来源版本构成依赖，Preview解释必须重建、可在声明边界内保留及仍未知的对象。覆盖不足不是“不受影响”的证据。批准绑定精确预演、负责人和有效期；内部后继结果在同一事务生效，外部效果通过单独获准的请求和回查收敛，不承诺跨任意CRM的原子事务。

详细资料：[系统架构](../ARCHITECTURE.md)、[Apply事务](../APPLY-TRANSACTION.md)、[OAC边界](../OAC-ORGREBASE-PRODUCT-BOUNDARY.md)。这些深层文档保留其原始语言。
