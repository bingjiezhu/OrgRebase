# 变化待办与候选准备

OrgRebase 的变化日志已经是待处理工作的规范来源，Preview attempt 已经保存候选派发身份和未知结果。
`change-worker` 只把这两层连接起来：它为仍然有效、尚未预演的变化准备候选，不创建第二个队列，
也没有批准或 Apply 权。

## 员工待办

经过当前身份与工作区权限验证后：

```text
GET /api/workspace/change-work-items?after=0&limit=50
```

返回当前负责人、权威修订、阶段、可用操作、Preview/批准/补证状态。这个投影不返回来源值，
也不代表消息已经送达外部渠道。用户打开待办和执行动作时，服务器仍按当前成员、职责和版本重新鉴权。

## 后台准备候选

部署管理员保存一份私有策略文件：

```json
{
  "schema_version": "orgrebase.change-preparation.v1",
  "workspace_id": "default",
  "access_token_variable": "ORGREBASE_CHANGE_WORKER_TOKEN",
  "enabled": true,
  "provider_mode": "local-deterministic",
  "max_changes_per_run": 20,
  "dispatch_budget": {
    "schema_version": "orgrebase.deployment-dispatch-budget.v1",
    "deployment_scope": "replace-with-stable-deployment-id",
    "currency": "USD",
    "period_seconds": 3600,
    "max_reserved_microusd": 50000000,
    "max_reserved_calls": 100,
    "max_dispatches_per_period": 50,
    "max_queue_reservations_per_period": 50,
    "max_active_attempts": 2
  }
}
```

示例额度只是配置结构，不是建议的客户阈值。部署方应按自己的采购和运维决定替换全部数值，
并保持 `deployment_scope` 在同一部署中稳定。启用自动模型候选时必须配置该策略；本地确定性
模式也可配置它来验证同一持久派发边界。

文件必须是不可由组或其他用户写入的普通文件。先做只读计划：

```bash
orgrebase change-worker --config /private/config/change-preparation.json --dry-run
```

只读计划不创建候选尝试或预算预留，也不把过期预算记录持久改为 `RESULT_UNKNOWN`。

再由客户已有的作业管理器周期执行同一命令（去掉 `--dry-run`）。每次启动都会重新验证服务令牌、
工作区权限和策略摘要。原 v1 配置仍只使用本地确定性候选。v2 可显式选择 Vertex 候选准备；
先复制并审阅上述描述符，将`schema_version`设为`orgrebase.change-preparation.v2`、
`provider_mode`设为`vertex-ai`，保留经过部署方审定的正数周期费用/调用/派发/队列/并发上限。
这个改变本身不运行模型；也不批准或 Apply 候选。

v2 的进程环境必须同时明确`ORGREBASE_CHANGE_MODEL_PROVIDER=vertex-ai`、
`ORGREBASE_CHANGE_MODEL_ID=gemini-3.8-flash`和
`ORGREBASE_CHANGE_MODEL_BUDGET_PATH`指向有效的私有价格/单次额度合同；Vertex 项目与身份沿
[认证部署](AUTHENTICATED-DEPLOYMENT.md)配置。缺任一价格资格、价格过期、费用/调用上限为零、
运行服务的实际 provider 与私有策略不符，都在派发前拒绝；不会回退到其他模型。
部署周期预算、调用、派发、队列准入及并发上限使用既有 v6 台账与 Preview reservation
同事务预留。默认模型任务保持串行，旧的未决尝试按原身份回读，不能通过改策略重新发送。

v2 的协议接线已经在当前受控 SQLite/PostgreSQL 与模拟 Vertex HTTP 响应下核对；
实际 Vertex 身份、费用和输出仍须独立运行和记录，当前为`NOT_RUN`。日志中的
`VERTEX_V3_CANDIDATE_ONLY`只说明本轮选择了该受限候选协议，不说明客户环境已验收。

## 崩溃与重复运行

- Preview reservation 写入数据库之后才允许候选执行；同一事件的并发 worker 复用同一 attempt identity。
- 新台账身份同时绑定工作区与原 Preview attempt；两个工作区的同名事件独立结算，旧台账记录原样可读。
  重启或跨周期重复请求沿首次预留，不重扣一笔预算。PostgreSQL 对同一 tenant/deployment scope 使用事务
  advisory lock，SQLite 使用 `BEGIN IMMEDIATE`；额度失败时台账和 Preview reservation 一起回滚。
- 费用、调用、速率和队列额度按周期计算；活跃并发跨周期计算，上一周期未截止的任务仍占并发槽。
  更新部署时先停止旧 worker，再整体更新并恢复调度，避免混用旧周期锁与新范围锁。
- 发送窗口崩溃的额度到固定 deadline 后记为 `RESULT_UNKNOWN`。并发槽只在持久终态后释放；未知费用和
  调用仍永久计入原周期，不因重启、取消或新周期而改写历史。
- reservation 已存在但没有结果时，系统返回处理中或结果未知，不会换 run、nonce 或策略修订后重派。
- 已持久失败、在途或未知的 attempt 只计入输出的 `blocked_attempts`，不占本轮新准备上限，后续合法变化仍可处理。
  明确的单项执行失败在保存回执后记录 `BLOCKED` 并继续；当前授权、绑定完整性或部署预算错误仍停止扫描。
  失败待办可回读原操作并沿现有恢复流程处理，不会自动重新执行。
- worker 完成只生成零规范写入的 Preview。负责人批准和有权主体 Apply 仍走原 API。
- 补证继续保留原 `return-for-evidence` / `resume` 合同。旧调用不带新字段时仍同步准备候选；
  `defer_candidate_preparation=true` 会先原子保存精确补证与待执行意图，由 `change-worker`
  复用同一 recovery digest 和 attempt identity 只准备一次新候选。

候选生成、人工等待和外部效果分别观测。通知合同从当前 authority revision 生成最小摘要和重新鉴权
链接，逐项保存 `READY`、`DISPATCHING`、`SENT`/`SENT_UNKNOWN`、`DELIVERED`、`READ`；旧负责人或
已关闭待办在发送前失效。响应丢失保持 `SENT_UNKNOWN`，不会盲目重发，也不会重新执行模型或业务变化。
逻辑 `notification_id` 明确绑定 tenant、workspace、event/preview/authority revision、收件人 actor/ref、
channel id 与消息合同版本。`send_timeout_seconds` 与 `unknown_after_seconds` 只是运行参数，
修改它们不会生成第二个意图或派发 claim；首次 `created_at` 保留不改。同一审阅槽的
收件人映射、transport 或重新鉴权 origin 改变会显式冲突，不会被当成重试。通道异常只允许
固定公开 reason code；未识别代码、异常文本和远程响应统一记为
`NOTIFICATION_TRANSPORT_RESULT_UNKNOWN`。
仓库提供 transport 协议和受控替身验证，没有内置或已授权的客户消息通道；真实发送仍为 `NOT_RUN`。

## 当前范围

该入口不证明客户 IdP、真实来源、真实外部通知、生产吞吐或 ROI。普通本地演示仍可手工预演；企业环境
启用后台 worker 前，应完成认证部署、来源准入、运维告警与恢复演练。
