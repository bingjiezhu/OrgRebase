# Dataverse 草稿写回的部署与资格验证

本适配器只允许修改一个服务端配置的 Dataverse 报价草稿的 `name`、`description`。它不修改价格、行项目、客户、订单或报价状态。观察、提出变更、负责人批准、排队、执行、对账与取消复用现有工作区、身份授权和 Commit Gateway。

`orgrebase dataverse-target probe` 是只读前置检查。即使 `metadata_status=PASS`，报告仍为 `status=INCOMPLETE`、`production_ready=false`、`write_authorized=false`。它没有证明客户环境中的权限不可绕过、插件没有外部副作用或故障恢复已经合格。客户环境尚未执行的检查统一标为 `NOT_RUN`。

## 1. 配置与身份

部署身份、数据库、工作区目录与服务端成员映射沿用 [认证部署说明](AUTHENTICATED-DEPLOYMENT.md)。正式 worker 使用受限 PostgreSQL 运行角色，不能用数据库所有者或超级用户替代。工作区和配置文件由服务端目录决定；浏览器不能指定数据库、目标地址或凭据文件。

效果配置示例中的值均须由部署管理员替换：

```json
{
  "workspace_id": "default",
  "organization_id": "00000000-0000-0000-0000-000000000987",
  "owner_id": "human:quote-owner",
  "target_token_variable": "DATAVERSE_ACCESS_TOKEN",
  "access_token_variable": "ORGREBASE_WORKER_ACCESS_TOKEN",
  "approval_seconds": 900,
  "observation_seconds": 300,
  "target": {
    "tenant_id": "org:example",
    "instance_url": "https://example.crm.dynamics.com",
    "quote_id": "00000000-0000-0000-0000-000000000123",
    "receipt_entity_set": "orgrebase_effectreceipts",
    "receipt_primary_key": "orgrebase_effectreceiptid"
  }
}
```

这里的 `organization_id` 是目标 Dataverse `WhoAmI` 返回的 `OrganizationId`，不是 Microsoft Entra 租户 GUID，也不是 `tenant_id`。生产 worker 必须提供它，并将配置文件与服务端选定工作区的 `effect_config` 精确匹配。

两个 token 属于不同权限边界：OrgRebase worker token 证明当前工作区执行权限；Dataverse token 仅由目标适配器读取，用于固定目标环境。API 和浏览器不接收目标 token。现有部署身份系统负责签发和更新凭据；本模块没有另造 OAuth 客户端。不要将 token 放入配置 JSON、命令行参数、审批证据、截图或报告。

目标 URL 必须是 HTTPS origin。可选 `target.ca_bundle` 指定受信 CA 文件；默认使用系统受信 CA，始终验证证书和主机名。请求拒绝重定向、不继承环境代理、限制响应大小，不能以 `verify=false` 或代理跳转绕过环境绑定。

## 2. 由管理员创建回执表

先生成本地待审制品，此命令不访问目标系统：

```sh
orgrebase dataverse-target schema --output receipt-table-review.json
```

制品包含管理员待提交的 `EntityDefinitions` 请求体、预期主键和限制说明，`automatic_submission=false`。管理员需要在正确环境、带 `orgrebase` 发布者前缀的受控 solution 内审核和创建表。该 JSON 是部署建议，尚未在客户环境创建或获得真实服务器接受证明。创建后应重新读取实际元数据，不能以“文件生成成功”代替表已部署。Microsoft 的建表 API 说明见 [创建和更新表定义](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-update-entity-definitions-using-web-api)。

报价和回执必须位于同一 Dataverse 环境，均为 Standard、非虚拟表。回执主键必须是平台 GUID 主键，允许调用方在创建时指定 GUID；默认表名对应 `orgrebase_effectreceiptid`。不要把可重复的文本字段或另建的业务编号当作主键。生成的表使用 OrganizationOwned；这本身不提供只追加权限。

除 GUID 主键外，必须具备下列字符串字段，容量至少如下。容量是应用写入契约，不应擅自缩短。

| 字段 | 最小字符数 | 内容 |
| --- | ---: | --- |
| `orgrebase_name` | 36 | 确定性的回执 GUID 文本 |
| `orgrebase_effectid` | 512 | 效果身份 |
| `orgrebase_requestdigest` | 71 | 已批准请求摘要 |
| `orgrebase_tenantid` | 256 | OrgRebase 租户 |
| `orgrebase_targetkey` | 1024 | 固定报价资源 |
| `orgrebase_predecessorversion` | 512 | 批准时观察的 ETag |
| `orgrebase_approvaldigest` | 71 | 审批摘要 |
| `orgrebase_payloadhash` | 71 | 写入内容摘要 |
| `orgrebase_outcome` | 9 | `CONFIRMED` 或 `REJECTED`（取消墓碑） |

回执表需要允许 worker 创建、读取，拒绝覆盖和删除。必须评估所有可触达该表的身份、角色继承、应用用户、管理员、紧急权限、导入作业、插件和自动化；不能只检查正常 worker 的一个角色。字段元数据 `IsValidForCreate/Read/Update` 表示字段操作能力，**不等于当前主体的有效安全权限**，也不能用可写字段标志证明记录只追加。

## 3. 执行只读探测

由已有凭据管理方式向 worker 环境提供目标 token 后运行：

```sh
orgrebase dataverse-target probe \
  --config effect-worker.json \
  --organization-id 00000000-0000-0000-0000-000000000987 \
  --token-variable DATAVERSE_ACCESS_TOKEN \
  --output target-qualification.json
```

探测只发送 GET，读取 `WhoAmI`、表/字段元数据和目标草稿。报告记录元数据摘要、配置摘要、检查时间、目标和各项结论，不包含报价名称/描述原文或 token。

它严格核验以下事实：环境 GUID；实体集合和唯一 GUID 主键；`TableType=Standard`；`DataProviderId`、`DataSourceId` 明确存在且为空；字段类型与容量；报价乐观并发已启用；报价仍是可读取的 Draft 且包含有效 ETag。缺失属性、重复实体、重复字段或无法完整读取的元数据均不会默认为通过。当前探测拒绝元数据分页；遇到 `TARGET_METADATA_INCOMPLETE` 应调查环境响应，不应绕过验证。

退出码 `0` 仅表示上述元数据前置项通过；`1` 表示发现相矛盾的元数据；`2` 表示输入、连接或响应无法可靠验证。完整生产资格不能由退出码 `0` 推导。

正式 worker 在观察、首次 READY 执行、取消前重新运行该探测，并将 `target-metadata` 报告和 `TARGET_METADATA_CHECKED` 事件写入同一工作区。失败时不发送首次写入。QUERY 和崩溃后 DISPATCH 接管只查询既有结果，不借此再次发出原写入。

## 4. 正式运行的唯一流程

1. `orgrebase effect-worker --config effect-worker.json --observe` 读取目标草稿并保存有期限的观察。
2. 用户在工作区提出对该观察的精确变更；服务端限定字段与目标，生成不可变提案摘要。
3. 配置的负责人通过现有已验证 Principal 批准。审批绑定提案、请求、资源版本、负责人身份和有效期。
4. 当前 executor 排队 `EXECUTE`。API 不持有目标凭据，也不在 HTTP 请求中直接写回。
5. `orgrebase effect-worker --config effect-worker.json` 在每次处理时重新核验 worker、排队者和批准者的当前成员及工作区权限。唯一 Gateway 保存状态并调用目标适配器。
6. 适配器在同一个 changeset 内先插入确定 GUID 的回执，再以精确 `If-Match` 更新报价。成功仍通过相同回执核对绑定结果；不能把外层 HTTP 200 当作成功证明。

目标回读的 `name`、`description` 只接收文本或空值；返回对象、数组、布尔值或数值时，以
`TARGET_QUOTE_FIELDS_INVALID` 拒绝本次观察，不写入观察制品，也不据此派发草稿更新。
合法的已有长文本仍可读取，不能把本适配器的写入长度上限误用为租户现有内容的读取上限。

`CONFIRMED` 表示回执绑定的那次提交已完成，不证明目标之后没有被其他人员或系统修改。
控制台“刷新操作记录”读取本系统已保存的记录；QUERY 核对历史回执。需要核对目标现值时，
重新运行第 1 步的 `--observe`，按新观察的时间、版本和字段值检查。回读失败不能用旧回执
替代当前观察，也不会撤销历史提交事实或自动重发原操作。当前没有持续目标漂移监控。

Microsoft 为符合条件的 changeset 提供事务语义，但 [Elastic tables 已知限制](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/elastic-tables) 明确指出相关批次可能成功却不具备原子性，因此本适配器拒绝 Elastic。协议依据见 [changeset 批处理](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api) 与 [ETag 条件操作](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/perform-conditional-operations-using-web-api)。元数据类型依据见 [EntityMetadata](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/entitymetadata?view=dataverse-latest)。这些平台文档仍不能代替对客户插件和权限的验证。

超时、断连、回执缺失或不匹配进入 `COMMIT_UNKNOWN`，维持目标屏障。用户可排队 QUERY；它只对账，缺少回执不代表没有执行。需要取消时，由负责人发起 CANCEL，在相同 GUID 主键写入取消墓碑。其成功含义是阻止尚未提交的原 changeset，不能撤销已提交写入。不要删除 UNKNOWN 记录、改回 READY、换 effect ID 重发或手工删除回执以“解卡”。

## 5. 真实沙箱的必验矩阵

以下项目在本地协议服务通过后仍为 `NOT_RUN`。必须在获授权的客户 Dataverse 沙箱执行，记录环境 GUID、solution/插件/权限版本、主体、精确请求/审批摘要、目标版本、故障时刻、回执、重启日志和只读结果。日志须脱敏，不收集 token。

| 报告检查码 | 操作 | 必须取得的证据 |
| --- | --- | --- |
| `RECEIPT_APPEND_ONLY_AUTHORITY` | 在部署身份和其他可触达主体下分别尝试创建、读取、修改、删除、upsert 覆盖；检查管理员和紧急权限控制。 | 允许的创建/读取实际成功；所有未授权覆盖/删除实际被拒绝；例外权限由独立保管和审计控制。不能只提交配置截图。 |
| `TARGET_CUSTOMIZATION_ISOLATION` | 盘点并受控隔离 quote/receipt 的同步插件、异步 flow、作业及跨系统副作用。 | 导出的完整配置、受控变更版本和故障下的副作用检查；数据库回滚不等于插件外发消息被撤销。 |
| `LIVE_ATOMIC_ROLLBACK` | 在提案观察后改变报价版本，再执行批准的旧请求。 | 报价保持新版本；旧 If-Match 被拒绝；同 changeset 回执不存在，未发生部分提交。 |
| `LIVE_RESPONSE_LOSS` | 在目标提交后丢弃响应，终止并重启 worker，发起 QUERY。 | 首次 UNKNOWN；原回执能精确确认；报价只变更一次，重启进程没有再发批次。 |
| `LIVE_LATE_COMMIT_CANCEL` | 延迟原批次到达/提交，形成 UNKNOWN，确认尚无结果后创建同主键取消墓碑，再释放原批次。 | 原批次因主键冲突整体失败；墓碑保留；报价未被延迟批次修改。 |
| `LIVE_CONCURRENT_WORKERS` | 两个实际 worker 竞争同一命令，同时设置响应延迟与租约接管。 | 只有一个稳定 effect 身份；接管只查询；UNKNOWN 屏障在有明确结果前保持。 |
| `LIVE_PERMISSION_REVOCATION` | 分别在排队后、发送前、目标请求进行中撤销批准者/worker/目标权限。 | 发送前撤销被拒绝；已在途请求按真实目标结果对账；不声称能撤销已提交事务或即时感知未送达的 IdP 撤销。 |
| `RECEIPT_RETENTION_AND_RECOVERY` | 恢复早于部分效果的数据库备份，并保留当前目标回执；清点恢复窗口内所有回执，包括本地已丢失意图。 | 丢失意图被发现并隔离；相矛盾、遗漏或无法完整分页的清单阻止恢复写入；有完整当前身份和对账证据后才由受控恢复流程释放。 |

报告不接受通过勾选这些行来将 `production_ready` 改为 true。真实环境验收记录由部署方独立留存，并纳入其上线批准和持续变更控制。本项目的本地 HTTPS/签名 JWT/受限 PostgreSQL 测试证明真实传输与应用控制链，目标服务器仍是受控协议模型，不能证明真实 Dataverse 的故障表现。

## 6. 保留与恢复

目标回执及取消墓碑的保留期限必须覆盖数据库备份、PITR、外部请求最大延迟和审计所需的完整窗口。无法给出上界时，不能按短期日志策略自动删除。恢复旧数据库不允许重新创建旧用户会话、抹去当前删除记录或把所有效果当成待执行。

适配器提供固定租户、固定目标、固定时间范围的只读回执分页，用于发现备份后已提交但本地意图丢失的效果。只查本地现存 effect 不足以证明恢复完整；回执清单也不能凭空重建缺失的批准内容。采用现有恢复隔离和对账流程，完整查明差异前暂停该范围的新写入，不以本探测报告直接解除隔离。

元数据、权限、solution、插件、凭据用途或回执保留策略发生变更后，应重新执行对应检查。过去的一次 PASS 只支持当时配置，不构成永久资格。
