# 恢复副本的外部效果盘点

数据库恢复成功不表示可以恢复外部写入。备份之后发生过的效果可能已经存在于 Dataverse，但它对应的本地 intent 已被恢复时间点截掉。只遍历恢复库中的 intent 会漏掉这些效果。

`orgrebase recovery-inventory` 从目标的追加式效果回执表开始，在明确时间窗内分页读取，再与恢复库全部工作区的本地账本比对。它只读，要求数据库已处于 `recovery_required` 隔离状态，并在任何目标网络请求之前确认操作者具备跨工作区盘点权限。普通运行角色不能借这个命令扩大范围。

```sh
orgrebase recovery-inventory \
  --target-config /private/deployment/dataverse-target.json \
  --database-url-env ORGREBASE_RECOVERY_DATABASE \
  --tenant-id tenant:example \
  --token-variable ORGREBASE_RECOVERY_TARGET_TOKEN \
  --since 2026-09-01T00:00:00Z \
  --until 2026-09-09T00:00:00Z \
  --max-pages 1000 \
  --output /private/recovery/new-inventory.json
```

目标配置是 `DataverseDraftTargetSettings` 对象。数据库管理员和目标只读 token 由环境变量提供；报告不含凭据。输出文件必须是新文件。时间窗至少覆盖所选备份、最后可信审计锚、可能在途的旧 worker 及预计的恢复缺口；不能用随意缩短时间窗证明没有丢失效果。

## 报告如何解释

| 分类 | 含义与后续动作 |
|---|---|
| `MISSING_FROM_RESTORED_DATABASE` | 目标回执存在，本地 intent 缺失。需要从独立保留的请求及授权账本恢复事实，不能用摘要反造批准 |
| `MATCHED_TERMINAL_INTENT` | 本地终态与目标正向回执一致；不代表所有其他效果都已对账 |
| `REQUIRES_RECONCILIATION` | 已找到正向回执，原 intent 尚未形成一致终态；走原效果查询/对账路径 |
| `BINDING_OR_OUTCOME_CONFLICT` | 目标或本地范围、前置版本、请求摘要、回执身份或结果冲突；保持隔离并调查 |
| `UNRESOLVED_NO_RECEIPT` | 当前未找到正向回执；这是 UNKNOWN，不能据此断言没有发生写入 |

回执必须符合 tenant、目标、时间窗、确定性 operation UUID、请求摘要、payload 摘要及 ETag 绑定。每页最多 100 条；后继游标必须保持原 HTTPS origin、路径和查询范围。分页预算耗尽、循环、畸形链接、网络失败或范围不符使盘点不完整。

`INVENTORY_COMPLETE` 仅表示完成所选有界盘点，**不表示恢复准入通过**。所有报告保持 `writes_released=false`，命令既不修改 intent，也不释放目标屏障或恢复隔离。

## 解除隔离前的实际工作

1. 在部署层停止或隔离旧 worker 的写入出口；数据库恢复不能撤回它已持有的网络凭据。
2. 在新数据库恢复备份，重放独立保留的最新删除台账，验证可信签署的审计检查点和当前授权。
3. 为每个配置目标执行覆盖恢复缺口的盘点，核对跨工作区共享目标；处理缺失 intent、冲突及 UNKNOWN。
4. 对真实 Dataverse 完成标准表、追加式回执 ACL、条件写、插件副作用、故障注入及只读回查资格。元数据检查单独通过不够。
5. 由部署责任人记录客户接受的恢复点、未解决风险、凭据轮换和恢复时间，再按既有变更管理解除隔离。

原始请求/批准若没有被独立保留，不能仅从远端摘要重建授权。这时保留隔离并人工处理是真实结果。当前命令提供可复用盘点机制，客户 RPO/RTO、凭据隔离和签收仍需真实部署验证。

本地测试包含真实 PostgreSQL 原生备份和恢复：在备份之后创建 intent 与外部回执，恢复到新库后发现本地已丢失的 intent，并验证原库未受影响、恢复副本仍被隔离。协议负例覆盖分页越界、错绑和无回执；这些测试不声称已访问客户 Dataverse。

相关：[状态存储迁移](STATE-STORE-MIGRATIONS.md)、[审计检查点](AUDIT-CHECKPOINTS.md)、[目标资格](DATAVERSE-TARGET-OPERATIONS.md)。
