# 负责人变更预览

负责人交接前，可以查看一个现有资源将由谁接管、哪些已记录的依赖工作需要复核、哪些待批事件不能直接继承，以及哪些外部执行必须先核对结果。

这是只读预览。返回候选内容摘要和建议的 `EnterpriseBinding`，但 `activation_allowed` 恒为 `false`。不会修改资源、负责人配置、来源版本、批准、执行记录或历史；也不会调用模型、发送外部请求或创建数据库记录。

## 请求

`POST /api/workspace/organization/owner-change-preview`

```json
{
  "slot_id": "product_plan",
  "expected_binding_digest": "sha256:<当前 EnterpriseBinding 的摘要>",
  "expected_snapshot_digest": "sha256:<当前图快照的摘要>",
  "proposed_owner": {
    "issuer": "https://identity.example",
    "subject": "employee-subject",
    "actor_id": "user:next-owner"
  },
  "reason": "产品负责人交接"
}
```

客户端应从当前工作区读取上述两个摘要，不能用上次预览返回的建议 binding 摘要代替当前摘要。资源仅能通过现有 `slot_id` 选择，不能通过该接口新增资源。

请求者必须是具有 `propose` 权限的可信身份；请求者和新负责人都必须通过现有成员校验及工作区授权。新负责人还必须通过 `approve` 权限校验。请求中的身份只是待核验输入，不会创建或授予身份。没有真实身份与工作区授权回调时，即使本地模式允许其他操作，本接口也拒绝预览。

## 阅读结果

| 字段 | 含义 |
| --- | --- |
| `digest`、`request_digest` | 本次预览全部内容的摘要，以及输入请求的摘要 |
| `resource`、`source` | 现有资源、原负责人、当前来源版本及摘要 |
| `proposed_owner`、`proposed_binding` | 已核验的新负责人和只替换这一项负责人的建议配置；配置内包含新摘要 |
| `dependent_work` | 当前图快照中，沿已准入依赖关系可达的工作目标；需要人工复核，不自动判定失效或重建 |
| `pending_events` | 本次检查范围内与资源及其下游有关的未完成事件；原负责人和原批准保持原状，未来迁移获批后仍需重新规划与授权 |
| `unresolved_effects` | 工作区内尚未发送、发送中或结果未知的效果；与所选资源的关联未得到确认，作为保守交接提醒列出 |
| `*_coverage` | 已检查和已返回数量、范围是否查完及未读分页位置 |

`READY` 表示原执行意图需要复核，不表示这份预览授予发送权限。`DISPATCHING` 和 `COMMIT_UNKNOWN` 必须先核对原 `effect_id` 的结果，不能因为换了负责人就生成新编号重发。本接口不会接管、取消、重试这些执行。

依赖分析直接复用现有 `ImpactEngine` 和当前快照中的版本、清单、已准入证据；不建立另一套业务影响或批准规则。`COMPLETE_WITHIN_RECORDED_SCOPE` 仅表示相应已记录范围的检查完成，不证明企业全部流程都已接入。图证明不完整、达到深度限制或结果截断时返回 `UNKNOWN`。

待批事件只检查现有事件日志的前 100 条，外部效果只检查工作区相关状态的前 100 条；不会循环扫描完整历史。任一页还有后续数据时，明确返回 `UNKNOWN` 和 `next_cursor`，不得把当前页中没有相关项解释成“没有待交接工作”。当前预览接口不接收继续分页参数；需要更多明细时使用已有事件与效果列表，再重新获取当前预览。

## 正式移交：部署明确启用后使用

预览 V1 的 `activation_allowed=false` 保持不变。正式移交使用独立的组织治理提案和确认，不能把预览摘要或普通报价批准直接当作迁移授权。

`ORGREBASE_OWNER_CHANGE_POLICY` 默认 `disabled`。部署组织明确选择 `mutual-consent-v1` 后，才允许创建正式提案：现任负责人授权移交，继任负责人独立接受。两人都必须通过当前可信成员的 `approve` 校验和工作区授权；两份确认绑定相同提案、职责版本、来源版本、组织快照、政策及一小时有效期。激活时重新核验双方权限。管理员角色没有代签权，原负责人不可用时不能绕过确认。

继任者可以不在初始 `profile.governance.owner_refs` 中，但必须是现有、已核验的成员。此流程不改变成员角色，不修改 seed profile 或已准入 pack，不移交目标系统操作负责人。启用内置政策只是部署选择，不表示该政策已经获得真实企业认可。

| 顺序 | 接口 | 权限与输入 |
| --- | --- | --- |
| 读取发起选项 | `GET /api/workspace/organization/owner-change-options` | `read`；当前职责与快照、部署政策、发起权限；只有有权发起者取得经 `approve` 及工作区成员校验的继任目录 |
| 查看移交列表 | `GET /api/workspace/organization/owner-changes?limit=20&after=...` | `read`；键集分页，每页最多 100 条，返回 `next_cursor` |
| 创建提案 | `POST /api/workspace/organization/owner-changes` | `propose`；使用预览请求字段，增加唯一 `proposal_id` |
| 查看 | `GET /api/workspace/organization/owner-changes/{proposal_id}` | `read`；返回完整提案、摘要、双方确认和激活回执 |
| 双方分别确认 | `POST /api/workspace/organization/owner-changes/{proposal_id}/confirm` | `approve`；正文为 `{"proposal_digest":"sha256:..."}`；服务器依据可信身份区分授权和接受 |
| 正式生效 | `POST /api/workspace/organization/owner-changes/{proposal_id}/activate` | 当前两位确认人之一，以 `approve` 身份提交同一摘要；两份有效确认缺一不可 |

`proposal_id` 不能复用来改内容。同一激活重复提交返回原回执，不再切换版本。候选过期、当前 binding/来源/快照漂移或人员失去权限时，不能继续推进；重新创建候选并重新确认。没有组织宪制替代审批、定时批量移交或跨工作区迁移。

控制台入口为“变化处置 → 业务数据演化与验收 → 职责移交”。选取职责与可信继任者、预览、登记后，两位负责人分别使用自己的账号打开同一提案 ID。详情中的 `allowed_actions` 来自服务端当前身份校验；`confirmation_role` 指明现任授权或继任接受，不能由浏览器代选身份。已经完成本人的确认时不再显示重复确认按钮，双方确认后才可能出现激活。

读取列表、选项与详情均复用只读快照，不创建职责版本或确认。写请求失去结果时先刷新核对同一 ID；登记恢复保留原 ID 与完整输入，不自动创建新提案。读取成功不赋予写入权限，正式确认与激活仍重新检查当前条件。已失效或生效的历史持续可读。

## 生效后的行为

当前职责只有一个权威入口：现有 `StateStore` 的对象版本和 current pointer。初始 r1 artifact 保留为历史依据；首次激活才建立职责版本指针，后续通过事务和 CAS 切换。服务和 worker 读取同一数据库状态，重启不会恢复 seed 中的旧负责人。每次回转都产生新版本，因此 A→B→A 不会恢复旧批准。

- 资源上旧的未完成事件、预览、批准和委派保留原字节，但再次推进要求重新规划。状态显示 `EXPIRED`，具体拒绝原因是 `CHANGE_OWNER_REPLAN_REQUIRED`；使用既有 `revises_event_id` 创建后继事件，由新负责人重新批准。没有将旧签名复制给继任者。
- 每个资源单独记录职责版本标识。未迁移资源的事件不会只因另一资源更换负责人就失去效力。已应用历史仍按原版本和原身份读取。
- 来源映射需要重新发现和确认；外部 schema 即使没变化，职责版本变更也会形成新的发现代次。长驻旧 worker 拒绝继续准入；新 worker 可以复用原始观察证据，为新职责创建新的事件身份。
- 外部操作的 `READY` 尚未派发，需要核对当前组织版本；迁移后重新提案。由于外部效果与单个来源资源的关系还未充分建模，这里保守要求整个工作区 binding 版本一致。
- `DISPATCHING` 表示已认领，不能据此断言网络请求已经发送；`COMMIT_UNKNOWN` 表示结果未知。这些记录、原始 `effect_id`、请求摘要和目标屏障不变，仍通过原授权主体查询原结果。迁移不自动重发、不取消、不把结果未知变成成功，也不替换目标配置中的 `owner_id`。

组织迁移、事件准入、正式 Apply 和首次 effect claim 使用同一工作区作用域锁。首次派发在既有事务中重新检查授权后认领，外部网络调用仍在事务外。

## 实现与验证边界

实现入口是 `workspace.owner_change.preview_owner_change()`；类型为 `OwnerChangePreviewInput` 与 `OwnerChangePreview`。身份、绑定、来源与依赖读取在工作区命令锁和数据库只读快照内完成。数据库查询使用现有图与分页接口，不写入候选、不产生新的当前负责人。

主要拒绝原因：`OWNER_CHANGE_BINDING_CHANGED`、`OWNER_CHANGE_SNAPSHOT_CHANGED`、`OWNER_CHANGE_SOURCE_CHANGED`、`OWNER_CHANGE_SNAPSHOT_REQUIRED`、`OWNER_CHANGE_RESOURCE_UNKNOWN`、`OWNER_CHANGE_OWNER_UNCHANGED`。HTTP 沿用现有变更路由合同：这些完整性拒绝返回 409，`detail.code` 和 `detail.message` 均为上述具体稳定原因；不公开原始异常文本。主 API 的类错误码与具体原因分层格式仍保持原样。成员、身份和工作区拒绝沿用现有 `AUTH_*` 错误，不为此接口另设异常体系。

专用 SQLite 测试比较操作前后完整逻辑数据库，覆盖可信身份、成员及工作区权限、冻结版本漂移、只改一个建议负责人、待批事项、未知外部结果和分页截断。成员校验使用本地可信模拟；这些测试不证明外部 IAM、真实客户组织政策或生产迁移已经验收。
