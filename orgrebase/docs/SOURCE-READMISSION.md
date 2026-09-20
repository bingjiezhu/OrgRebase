# 来源恢复组

来源删除、空值或资格失效后，旧事实保持失效。恢复先登记带精确当前版本、摘要和新读取事实的 READMIT 提案。一个来源使用通常的预演、批准和应用流程；同一工作区、同一报价的多个来源同时失效时，可以把 2–3 个提案组成恢复组，覆盖 `launch_date`、`currency`、`product_plan` 中不同的 slot。

恢复组是同一个 Rebase 内核的多来源输入，不是第二套执行器。组绑定来源事件摘要、当前图、报价前序、每项变化和预演。各原负责人分别批准自己的来源范围；同一负责人负责多项时一次批准覆盖其全部组内来源。合法委托仍绑定精确事件、主体、工作区、有效期和来源范围；一个负责人的批准不能代替另一个负责人。

批准齐全后仍需显式应用。事务内重新检查权限、委托撤销、有效期、来源版本、图与报价前序，以及当前声明的读取依赖。全部来源提升、一次报价重建、图后继、组结果、基础回执和幂等记录一起提交；任何失败整体回滚。提交前再次检查授权与有效期。请求响应丢失后以同一组命令重试，只能返回已经由持久回执和事件链确认的结果。

工作台的“来源恢复”区域按服务端允许动作显示待选来源、共同预演、各负责人批准和最终应用。任何组内负责人都可以明确拒绝该组，保留拒绝理由和失效来源；修订必须登记新事件再组成新组。组成员不能绕回单项入口重复应用。过期、版本漂移或运行时不兼容的组不能沿用旧批准。

| 接口 | 用途 |
| --- | --- |
| `GET /api/workspace/source-readmission-options` | 当前可入组的事件身份、来源范围与允许动作 |
| `GET /api/workspace/source-readmission-groups` | 有界分页列出恢复组 |
| `POST /api/workspace/source-readmission-groups` | 提交 `{group_id, events:[{event_id,event_digest}], reason}`，生成一次共同预演 |
| `GET /api/workspace/source-readmission-groups/{id}` | 读取精确组、来源事件、各负责人审批、结果与允许动作 |
| `POST /api/workspace/source-readmission-groups/{id}/approve` | 提交 `{group_digest, preview_digest, owner_id}`，批准当前主体负责的来源范围 |
| `POST /api/workspace/source-readmission-groups/{id}/reject` | 同样绑定两个摘要和 `owner_id`，另提供 `reason` |
| `POST /api/workspace/source-readmission-groups/{id}/apply` | 提交 `{group_digest, preview_digest}`，显式执行已完整批准的组 |

生产身份来自统一会话或令牌，浏览器不得自行声明审批人。只有受控本地模式接受显式 `actor_id` 标签。HTTP 的工作区选择、CSRF 与角色校验和其他业务接口相同。

组及结果使用明确的 `orgrebase.source-readmission-group.v1`、`orgrebase.source-readmission-group-outcome.v1`；多来源基础回执使用 `orgrebase.rebase-receipt.v2` 和逐负责人审批集合。当前组归档使用 v3，每组只计一次报价重建，人类批准数按实际来源负责人计。旧单来源回执和历史归档仍按各自明确的旧格式只读验证。

该能力不支持任意领域处理器、跨工作区原子恢复或不明确的来源范围。若同一目标被一个来源判定为确定受影响、另一个来源判定为不确定，则拒绝该共同预演；无关对象的不确定性保留人工核对状态。所有受影响的前提都必须重新具备有效资格，不能临时把来源改成 CURRENT 来绕过检查。客户 Dataverse、企业身份系统和真实运营指标仍需各自环境验收。
