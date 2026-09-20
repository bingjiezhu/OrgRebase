# Dataverse 只读源接入

源接入只产生观察和变更提案。字段映射必须先由对应负责人确认；源更新仍进入 `WorkspaceSourceAdmission → ChangeEvent → preview → approve → apply`，不会自动覆盖当前业务事实或取得目标写权限。

尚未应用的来源提案必须继续匹配当前已提交覆盖中的精确记录、字段和 revision。后续同步取得新 revision 后，旧提案失效，即使值相同或已经恢复到当前已准入值；再次预演、批准和实际应用都重新检查。来源不可见或覆盖过期时也不能继续处理旧提案。revision 是不透明标识，不按大小或字符串顺序推断新旧。

这一检查针对待处理的来源提案，不重写已应用事实，也不会让普通人工候选按同字段互相覆盖。历史回执可继续读取；当前事实的同值新 revision 不会因此使其他字段的正常工作永久阻断。

## 管理员限定范围

生产环境通过 `ORGREBASE_SOURCE_CONFIG` 指定配置文件；多工作区在服务端 workspace catalog 的相应条目指定 `source_config`。相对路径按 catalog 所在目录解析。文件必须是普通文件，不能是符号链接或被组/其他用户写入。每次实际源请求都会重查当前 worker 身份、工作区权限和配置摘要。

```json
{
  "workspace_id": "default",
  "organization_id": "00000000-0000-0000-0000-000000000987",
  "source": {
    "connector_id": "source:quote-dates",
    "tenant_id": "org:example",
    "instance_url": "https://example.crm.dynamics.com",
    "entity_set": "quotes",
    "record_ids": ["00000000-0000-0000-0000-000000000123"],
    "page_size": 100
  },
  "source_token_variable": "DATAVERSE_READ_ACCESS_TOKEN",
  "access_token_variable": "ORGREBASE_SOURCE_WORKER_TOKEN",
  "freshness_seconds": 300
}
```

示例标识须替换为实际值。`organization_id` 必须来自目标环境 `WhoAmI.OrganizationId`；`tenant_id` 是本产品租户。当前连接器只支持 Dataverse Standard、非虚拟的 `quotes` 表，要求开启 change tracking；没有通用任意表接入或定价引擎替代承诺。

管理员只限定来源、记录范围、凭据变量名和观察期限，配置中不接受 `mappings` 或预定业务 `fields`。旧的未经确认映射配置在生产 CLI 被拒绝。CLI `--config` 必须精确对应当前服务端工作区选定的路径。

源 token 与 OrgRebase worker token 分开管理。API 和浏览器不读取源 token。通过既有身份系统/凭据管理器签发与轮换，不将 token 写入 JSON、命令行、审批摘要或公开报告。HTTPS 始终验证证书和主机名；可选 `source.ca_bundle` 指定受信 CA，不能关闭验证。源请求不继承环境代理、不跟随重定向，游标始终固定在已配置 origin 的 `/api/data/v9.2/quotes`。

## 发现、选择和负责人确认

```sh
orgrebase source-sync --config source-config.json --discover
```

发现只读取环境和表/字段元数据，保存内容摘要及观察时间。它不会下载未来业务变化、注册变更事件或写 Dataverse。网页不能提交“可信元数据”；库存仅由持有源只读凭据的 worker 获取。

系统按现有模板 slot、字段逻辑名及显示名提出候选。候选只是辅助选择，显示名不构成权限或语义权威。负责人只能来自现有 `EnterpriseBinding`，并须在当前服务端成员目录中具有 approve 权限及该工作区 scope。没有可用负责人或没有可自动匹配字段，会展示具体 slot/字段缺口。

当前映射支持 String/Memo 的直接字符串和 `DateTimeBehavior=DateOnly` 的显式 `date_only` 转换。对于日期，输入仅接受合法 `YYYY-MM-DD` 或零时 UTC 表示，不能静默丢弃 UserLocal/TimeZoneIndependent 的时区语义。Lookup、Choice、Money 等需要各自的语义适配，当前显示不支持，不把它们偷偷当字符串。[Microsoft DateTimeBehavior 说明](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/datetimebehavior?view=dataverse-latest)区分了三种日期行为。

已有字符串代码需要规范化时，可在同一映射中提交最多 128 项的精确 `value_map`，例如源字符串代码到业务字符串值。它同样进入负责人确认摘要，未列出的值形成明确缺口；它不是任意脚本，也不能让不支持的字段类型绕过元数据检查。

HTTP 接口沿用现有 Principal、工作区选择和 BFF CSRF：

| 接口 | 作用 |
| --- | --- |
| `GET /api/workspace/source-binding` | 当前元数据、候选、提案、负责人决策、具体缺口和覆盖证据 |
| `POST /api/workspace/source-binding/proposals` | 选择允许 record、field、slot、transform，并绑定精确 inventory/generation 摘要 |
| `POST /api/workspace/source-binding/proposals/{id}/confirm` | 该映射中实际负责人确认精确 proposal 摘要；`id` 为摘要去掉 `sha256:` 前缀 |
| `POST /api/workspace/source-binding/proposals/{id}/revoke` | 原负责人撤销；不要求先恢复源连接 |

提案可以覆盖部分 slot；未映射对象仍是明确的未接入范围，不会因此宣称覆盖整家企业。多名负责人涉及的提案必须全部确认才生效。浏览器不能改变 owner 或扩大管理员允许的 record 范围。撤销不能在原提案上覆盖为确认；修订须形成新提案，重新确认。

字段元数据漂移会使旧确认失效；即使后来变回原样，也属于新的世代，不能悄悄复活旧确认。每次同步还会重新校验已确认人的角色、subject/actor 绑定与工作区权限。观察过期时不能新批准映射，但负责人仍可撤销。

## 增量同步和覆盖证据

先完成工作区初始成果形成，然后执行：

```sh
orgrebase source-sync --config source-config.json --max-pages 10
```

worker 只消费已确认映射，使用同一持久 inbox、租约、fence、游标 CAS 和业务工作流。每个确认世代使用独立 connector checkpoint；新映射不会把旧游标解释为新字段的完整基线。失败的持久页不能换一套映射再次解释，过期或已删除的 inbox 也不能从源重抓并冒充原页。

Dataverse change tracking 不支持 `$filter/$orderby/$expand/$top`。因此请求使用允许字段投影，客户端在入库前按配置的 record 范围筛选，其他记录原文不进入持久 inbox；部署时还应将源账号限制到必要数据。此实现不是服务器端行过滤承诺。[Microsoft change tracking 约束](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/use-change-tracking-synchronize-data-external-systems)提供协议依据。

每轮分页完成后，worker 对**已确认映射实际涉及的记录**逐一点 GET，确认当前可见性、opaque ETag 和字段值。上限为 32 条，超过范围必须分批设计绑定，不能静默截断。请求使用同一 reader 与身份复验。点读发生在页提交事务之前；页准入和覆盖证据、游标提交在同一事务。任何失败不会把游标提前推进。

`orgrebase.source-coverage.v1` 来自持久已提交页，含 source/config/binding/inventory/generation 摘要、精确 record/field 范围、游标 revision/digest、page 引用和摘要、逐记录 opaque revision/字段值摘要、观察时间和到期时间。它不含源字段原文或原始游标。没有“客户端说已经完整”入口。

以下情况为 `UNKNOWN`：分页未完成；允许范围记录尚未观测；点读 404 或权限拒绝；游标过期；字段/schema 漂移；确认权限撤销；观察过期；页与覆盖或配置绑定不一致。404 无法区分删除和权限不可见，不能据此断言记录不存在。历史删除 tombstone 也不能单独证明该记录现在不存在。查询 `is_missing` 仅允许“当前实际读到的记录，其允许字段明确为 null”；没有返回字段、没有返回记录均为未知。

覆盖完整表示在精确范围和有效期内已完成必要观察，不表示跨记录、跨系统的全局原子快照或即时 IAM 撤销。点读到 apply 之间仍有观察时效窗口。实际客户需要验证源角色的行/字段权限、change tracking 保留期、限流与故障表现，并为业务选定合适的 `freshness_seconds`（30–900 秒）。

只读源资格与 [目标写回资格](DATAVERSE-TARGET-OPERATIONS.md) 分开验证。源接入成功不会自动打开外部写入。
