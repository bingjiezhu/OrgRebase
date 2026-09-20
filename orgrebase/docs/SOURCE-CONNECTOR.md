# Dataverse 只读接入

当前连接器实现 Dynamics 365 / Dataverse `quotes` 的只读变更跟踪协议。真实客户实例、字段含义、账号权限和负责人验收尚未提供，因此实现和协议测试通过不能把连接器标为客户已验收。它没有报价定价、税费计算或正式写回权限。

源系统字段仍由客户负责。`new_currency` 等自定义字段只是配置示例；不得把 `transactioncurrencyid` 的 GUID 当 ISO 币种，也不得把报价有效日期擅自解释为产品发布日期。需要转换时，`value_map` 仅采用负责人确认的显式映射；缺少映射、空值、删除和未知类型产生具体来源缺口。

## 运行

使用 [认证部署配置](AUTHENTICATED-DEPLOYMENT.md)，已准入的企业包与数据库组织必须一致。API 只使用自己的身份配置；只读 worker 单独取得 Dataverse 的 read scope token。

先用已准入的初始事实形成基线报价，再启动 worker。形成之前返回 `SOURCE_BASELINE_FORMATION_REQUIRED`，不会登记缺口或推进游标。当前来源重新准入依赖已有报价的依赖图；它不能代替首次接入时负责人对初始事实的确认。

```json
{
  "source": {
    "connector_id": "dataverse:quote-currency",
    "tenant_id": "organization:your-company",
    "instance_url": "https://your-company.crm.dynamics.com",
    "record_ids": ["00000000-0000-0000-0000-000000000001"],
    "fields": ["new_currency"],
    "page_size": 100
  },
  "mappings": [
    {
      "record_id": "00000000-0000-0000-0000-000000000001",
      "field": "new_currency",
      "slot_id": "currency",
      "value_map": {}
    }
  ],
  "source_token_variable": "DATAVERSE_READ_ACCESS_TOKEN",
  "access_token_variable": "ORGREBASE_ACCESS_TOKEN"
}
```

配置文件只记录凭据环境变量的名称，真实凭据由组织秘密管理注入。OrgRebase 的短时 access token 必须属于服务端成员配置内有 `operator` 权限的工作身份；每页接收和事务中的变更准入重新验证。

```sh
orgrebase source-sync --config /private/config/dataverse.json --max-pages 10
```

该命令最多处理指定页数。还有分页时返回 `MORE_PAGES_PENDING`；运行者再次调用即可续传。限流返回带 `retry_after` 的稳定错误，调度者遵守该期限。当前没有另起定时器或第二个调度系统。

## 数据与恢复规则

同一连接器配置首次使用后锁定；组织、字段、记录范围、字段到 slot 的映射、值转换以及企业/领域绑定均纳入摘要。改变这些语义要求新的资格与连接器身份，不能用旧游标或未完成 inbox 按新含义解释旧记录。游标属于确切组织源地址和 `quotes` 路径，重定向及跨源游标被拒绝。ETag 和 delta link 都按不透明字符串保存，不解析大小、不自行构造下一版本。

获取一页后，先把受配置记录范围约束的页面保存为私有 inbox。页内事件准入、源版本候选和游标确认属于同一个数据库事务。中途失败会回滚整页接收；重试使用已保存页面，不重新采样并改变之前的事件载荷。worker 租约和 fence 阻止过期进程推进游标。

页面使用统一 `private_records` 存储，遵守 `ORGREBASE_PRIVATE_RETENTION_SECONDS`，默认 24 小时，最长 7 天。该 worker 必须保留尚未接收成功的页面，因此保留时间为 0 时拒绝启动同步。过期正文由统一清理入口物理清除；未接收成功的页面若已过期或删除，返回 `SOURCE_INBOX_UNAVAILABLE_RECONCILIATION_REQUIRED`，不重新抓取替换原内容，也不自动推进游标。应由负责人核对源覆盖与未接收事件后，以新的连接器资格重新初始化。已接收的业务候选和来源摘要属于业务记录，采用业务审计保留策略。

接入只生成源变化提案，调用与 HTTP 相同的 `register_change`，不能直接提升来源或报价版本。批准和应用仍需业务负责人确认精确内容。源值空缺或删除调用统一失效入口；报价显示需要重新确认，不能继续导出为有效成果。明确的重新准入提案仍经过同一批准和应用流程。

字段/schema 漂移、读取权限丢失、游标过期或来源不可用也通过同一入口登记来源缺口，保留准确原因，不把权限错误解释成字段不存在。普通限流只返回调度期限，不伪造源删除。接入身份失效时不能借错误处理继续修改业务状态。多源同时失效时，可以把同工作区、同报价的 2–3 个已登记 READMIT 提案组成[来源恢复组](SOURCE-READMISSION.md)。每个负责人分别批准自己负责的来源；同一执行内核在一个事务中重新核验全部来源、审批、依赖和版本，然后恢复来源并只重建一次报价。未覆盖的失效依赖仍阻止应用；不确定影响保持 UNKNOWN，不借另一来源的确定影响自动恢复信任。

完整性只覆盖配置的记录和字段。一次同步成功不证明字段业务含义正确、初始覆盖完整、所有外部依赖最新或客户愿意采用。初始同步、增量、来源保留窗口失效、真实账号撤销和负责人核对必须在客户实例分别验收。源页 inbox 是私有业务数据，部署必须按 [数据保留说明](WORKSPACE-DATA-AND-PRIVACY.md) 配置保留与备份范围。

## 协议依据

Dataverse 要求表启用 change tracking；增量使用服务端发出的 delta link，初次请求带 `Prefer: odata.track-changes`。该模式不支持 `$filter`、`$orderby`、`$expand`、`$top`；本实现使用 `$select` 并在保存前收窄配置范围。参见 [Microsoft 官方变更跟踪文档](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-change-tracking-synchronize-data-external-systems)。

限流和配额由客户实例决定，worker 不通过无限重试掩盖限流。参见 [Microsoft 服务保护限制](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/api-limits)。协议资料核对日期：2026-09-09。
