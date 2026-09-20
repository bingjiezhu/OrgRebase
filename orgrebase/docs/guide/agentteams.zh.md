# AgentTeams协作

OrgRebase使用固定AgentTeams源码身份。精确版本、commit与关键文件摘要见`agentteams/source-lock.json`和`agentteams/teamharness-lock.json`，不要仅凭“最新版”标签替换运行依赖。

## 角色与任务

Product、Legal、Finance与GTM分别处理自己的领域候选。当前原生参考路径的四领域Worker为确定性程序，Reviewer可调用配置的模型提供方；后续ChangeSet的独立契约复核是确定性检查。Reviewer检查领域集合、结果根与契约绑定，模型意见不具有最终批准权。领域上下文以任务和用途裁剪，法务受限原文不因GTM需要交付而自动开放。

原生任务链通过TeamHarness的projectflow/taskflow接口创建、委派、接收、提交、检查与完成任务。后续变化单独绑定ChangeSet、预览和任务回执；初次Formation与后续变化分别可查。

## 同一ChangeSet补证恢复 {#evidence-recovery}

原生执行模式下，尚未批准的独立提案可以由该提案的精确审批负责人退回补证，再由有提案权限的用户恢复执行：

1. 审批负责人选中待补证的领域任务，说明原因并指定该任务范围内的证据引用。
2. 有提案权限的用户补交接口列出的当前证据引用及精确摘要，并选择已注册的主执行实例或备用实例。
3. 系统保留原event、ChangeSet、前轮上下文与候选，新增恢复轮次、预览、独立复核和AT任务回执。
4. 负责人审阅新结果，批准时绑定恢复后的摘要；执行人再通过原Apply入口使报价生效。

补证只接受该任务已准入、状态为CURRENT或ACTIVE的同域来源对象，并把其完整内容与旧候选交给受限执行上下文。此入口不上传任意材料，不更换业务负责人。备用实例沿用已注册能力及同一提供方，当前以受控本地AT执行；它不等于企业人员权限移交或新的远程Agent服务。

| API | 输入与作用 |
|---|---|
| `GET /api/workspace/changes/{event_id}/recovery` | 读取状态、`allowed_actions`、任务/证据/执行实例选项、上下文摘要与历史轮次 |
| `POST /api/workspace/changes/{event_id}/return-for-evidence` | `operation_id`、`expected_context_digest`、`task_id`、`reason`、`required_evidence_refs`；要求精确审批负责人 |
| `POST /api/workspace/changes/{event_id}/resume` | `operation_id`、`recovery_digest`、`executor_id`及`evidence`中的`ref`/`digest`；要求提案权限 |

动作与字段以当前GET响应为准；Cookie会话仍需Origin和CSRF校验。恢复后，批准请求必须使用新的`recovery_digest`。相同操作身份和请求内容重放复用已有记录，不重派模型；明确FAILED后需负责人开启新一轮补证。RESULT_UNKNOWN不开放退回重派。已批准、已应用、已拒绝或成组提案不能通过此入口恢复。补证和复核均不写正式报价。

## 运行范围

云模型入口见[Vertex](models-vertex.zh.md)和[DeepSeek](models-deepseek.zh.md)。真实模型调用、本地AT任务链与生产分布式运行是不同事实。当前参考传输使用受控本地Matrix/对象存储，不等于已经部署了外部Element集群。

Element观察端只发布同运行的最小观察消息，不授予审批或Apply权限，也不证明Worker通过Matrix收到了任务。先用WebUI看业务结果，再查看任务、Tool和Skill证据。

相关原文：[Agent角色](../AGENT-IDENTITIES.md)、[Matrix观察](../MATRIX-OBSERVATION.md)。
