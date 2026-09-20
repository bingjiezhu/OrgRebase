# Workspace 原生模型候选运行说明

变化预览可以使用 OpenAI Responses 生成各领域的候选解释。模型只接收已准入的领域投影；GTM 接收已经核验的领域结果。候选仍经过独立验证、负责人批准和既有提交规则，不能自行修改组织规则、批准报价或调用目标系统。

本文描述当前代码的配置与恢复方式。协议夹具、受控本地测试和真实供应商调用是不同证据；本批实现没有运行真实模型请求，也不构成客户上线或收益证明。

## 启用方式

在服务启动前，通过现有部署配置设置下列变量。认证、数据库和来源配置继续使用 [认证部署说明](AUTHENTICATED-DEPLOYMENT.md)。

| 配置 | 当前行为 |
|---|---|
| `ORGREBASE_CHANGE_MODEL_PROVIDER` | 未设置时为 `local-deterministic`，变化轮使用本地参考候选；显式设置 `openai-responses` 才启用原生模型。其他值拒绝 |
| `ORGREBASE_CHANGE_MODEL_ID` | 原生模式必须明确指定账户可用的模型 ID；没有内置“最新模型”默认值，也不自动升级模型 |
| `ORGREBASE_CHANGE_MODEL_BUDGET_PATH` | 部署确认的冻结价格合同 JSON 路径，字段见下文；缺少合同时不派发真实付费调用 |
| `ORGREBASE_OPENAI_API_KEY` | 由部署的密钥设施注入服务进程；不要写入仓库、浏览器、命令记录、日志或导出材料 |

新预览在占用持久 attempt 前检查凭据、价格合同及整轮预留额度；缺失分别返回 `OPENAI_CREDENTIALS_MISSING` 或 `MODEL_PRICE_CONTRACT_REQUIRED`。这只是本地前置检查，不会探测账户权限、余额、实际价格或模型可用性。已经持久化的候选／预览在绑定仍有效时可直接读取，缓存读取不要求密钥。

原生模式使用固定 HTTPS 端点 `https://api.openai.com/v1/responses`，不接受来源正文提供的地址，不跟随重定向，也不读取环境代理。只发送当前任务范围的实际投影正文和结构化输出 schema；工具集合为空，`store=false`、`background=false`、`truncation=disabled`、`service_tier=default`。标准档位避免自动继承项目的其他服务档位；有价格合同的响应若缺少实际档位或返回其他档位，不准入候选。协议失败不会切换到 compatible adapter 或本地参考答案。[OpenAI 服务档位与输出上限](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

`store=false` 不等于账户启用了 Zero Data Retention，也不证明所有供应商侧处理均无保留。部署负责人应核对实际组织／项目、模型和端点的数据控制条件，参见 [OpenAI 数据控制说明](https://developers.openai.com/api/docs/guides/your-data)（核对日期：2026-09-12）。

## 请求、回执和保留

新通路使用 `ModelRequestV2`、`ModelResponseReceiptV2`。本次不增加改变历史 V1／V2 摘要的默认字段；价格预留进入现有 attempt 记录。新请求明确标准档位，因此实际 wire body 摘要和运行实现绑定随之更新；不能把新 wire body 倒写进旧回执。历史 V1 类型和摘要算法保持可读，不能把 V1 中的示例时间、请求模型版本或默认用量解释为本次模型调用的观察事实。

V2 绑定租户、工作区、任务、actor、purpose、run、nonce、领域、对象范围和投影内容摘要，同时保留模板、实际 prompt、实际请求正文及 schema 摘要。记录的 `requested_model_id` 与供应商返回的 `observed_model_id` 分开；供应商返回别名时，该别名不等于已证明不可变模型快照。

| `dispatch_state` | 可以得出的结论 |
|---|---|
| `NOT_SENT` | 尚未进入发送；例如缺凭据、输入合同不符或发送前取消，证据为 `NOT_RUN` |
| `SENT_UNKNOWN` | 已进入发送，但没有取得响应；请求可能已被接收或计费，证据为 `MODEL_ATTEMPT` |
| `RESPONSE_RECEIVED` | 已取得 HTTP 响应；仍可能是错误、拒绝、部分完成或读取中断 |

只有通过 schema 和观察信息检查的成功回执才为 `VALID`／`LIVE_MODEL`；它还需通过业务候选验证。无请求 ID、超时、429／5xx 或坏输出都不会产生可批准候选。`input_tokens`／`output_tokens` 为 `null` 表示未知，不能计作零费用。

请求、投影正文和回执作为候选工件保存在现有 StateStore 中，用于重启恢复和核对。它们不因此自动获得 `ORGREBASE_PRIVATE_RETENTION_SECONDS` 对 PrivateRecordStore 原始任务文本的清理语义。部署负责人需明确候选工件及其备份的访问和保留范围；运维日志只记录事件／attempt ID、摘要、公开错误码及已取得的供应商请求 ID。

## 一次预览如何执行

普通变更和多来源变更组共用同一套 attempt 机制：

1. 在短事务内捕获组织、来源、变化、运行配置和当前授权，检查完整领域与 GTM 调用数的最高费用，利用现有 StateStore 的同一幂等记录预留一次 attempt、执行截止和整轮费用上界。额度不足时整轮零派发。
2. 释放命令锁和数据库事务，按领域生成候选；每个领域一次，GTM 一次。每次真实 HTTP 请求由短命子进程执行，父进程按单调时钟执行剩余时间限制。验证器核验提交物，不重新调用模型，不要求解释文字完全相同。
3. 持久保存结果，再在短事务内检查当前授权、来源失效记录、事件状态、输入、运行配置和有效期。通过后才保存可审阅预览；已经过期或失效的晚到结果不能推进批准。

刷新、重复请求和进程恢复都复用同一 attempt。已有完整候选可以复用；存在未完成或失败记录时不会偷偷重发。不增加另一份数据库、后台重试队列或独立业务 writer。切换模型、prompt、schema 或实际运行配置会影响运行绑定，旧的未应用预览可能需要重新规划；已经应用的历史保持原回执。

## 预算与时间边界

当前服务使用以下默认上限；它们属于现有 adapter 配置，不是新增环境变量：

| 范围 | 默认值 |
|---|---|
| 单次 HTTP worker 调用截止／I/O timeout | 最多 30 秒，另受整轮剩余时间约束 |
| 单次请求／响应正文 | 262,144／1,048,576 字节 |
| 单次候选输出上限 | 2,048 tokens |
| 一轮候选调用／请求输出预算 | 最多 5 次／合计 10,240 tokens |
| 一轮输入正文合计 | 262,144 字节 |
| 一次 preview attempt 的模型调用截止 | 预留后最多 120 秒 |

金额范围是**一次 preview attempt 的完整领域与 GTM 候选轮**。它不是整个 workflow、企业、账户或月度资金账本；不会自动扣款、退款或查询供应商账单。价格合同必须由部署负责人依据选定模型与账户条款确认，不能用示例价格代替。

价格文件最多 16 KiB，拒绝重复字段、额外字段、无时区有效期、非有限金额和浮点数金额；金额使用十进制字符串。必需字段：

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定 `orgrebase.model-budget.v1` |
| `model_id` | 与明确选择的模型完全一致；不会替换用户模型；返回模型不符则拒绝候选 |
| `context_window_tokens` | 供应商公布的该模型上下文上限，作为每次输入的保守上界；不能填一个未经约束的较小输入估计 |
| `input_usd_per_million_ceiling` | 标准档位下，覆盖适用长上下文、普通输入与缓存写入等收费情形的每百万输入 token 最高美元费率 |
| `output_usd_per_million_ceiling` | 标准档位下，覆盖适用上下文范围的每百万输出 token 最高美元费率 |
| `max_preview_usd` | 整次 preview attempt 可预留的最高美元金额 |
| `price_source_ref`、`valid_until` | 价格合同依据及带时区的有效截止；过期合同拒绝新的派发 |

预留使用 `调用数 × (模型上下文上限 × 输入最高费率 + max_output_tokens × 输出最高费率)`，将结果向上取整为微美元，授权金额向下取整。`max_output_tokens` 已包括可见输出与 reasoning token，不再额外加一次 reasoning；输入上界也不把 cached token 重复相加。使用完整上下文上限有意偏保守，可能拒绝实际费用很低的请求；代码不把输入字节数换成未经证明的 token 上界。只有冻结合同准确且供应商按合同计费时，这个预留才有相应的费用上界意义；它不是实测账单。[OpenAI 输出 token 定义](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)、[价格与上下文档位](https://developers.openai.com/api/docs/pricing)

派发后失败、超时或缺用量不会释放原预留；已有回执逐次保留，`null` 用量仍是未知。完成后的缓存读取也不重新预留或调用。另开新事件必须是负责人明确修订后的新尝试，具有自己的整轮预留；不能冲销旧 UNKNOWN 或据此宣称整个 workflow 的总费用受同一金额限制。

HTTPX2 的 connect/read/write/pool timeout 分别约束阶段或数据块，本身不是总时限。因此真实网络执行放在受控子进程：父进程截止后停止子进程并关闭连接，拒绝任何晚到结果，进程回收等待另有最多 250 ms 上限。子进程也按同一单调时钟截止自行退出，避免父进程丢失后持续等待。这个边界覆盖本地模型网络调用，不是整个 HTTP 请求、数据库提交或操作系统调度的硬实时 SLA；注入的假 transport 只验证协议，不作为进程终止证据。[HTTPX2 超时定义](https://github.com/pydantic/httpx2/blob/main/docs/advanced/timeouts.md)

OpenAI 官方将关闭连接列为取消同步响应的方法；本实现维持同步响应，不改成 background 或增加远端轮询。关闭本地连接没有提供远端最终状态或计费撤回回执，因此丢响应仍保留 `SENT_UNKNOWN`／已收到的响应身份与未知用量，不记录“取消已成功、费用为零”。[OpenAI 同步取消边界](https://developers.openai.com/api/docs/guides/background)

## 未完成与失败的处理

| 观察结果 | 操作 |
|---|---|
| 缺凭据、模型 ID 缺失或配置无效 | 部署负责人修复配置。尚未预留 attempt 时，可重新请求预览 |
| `WORKSPACE_ADVISORY_IN_PROGRESS` | 同一 attempt 已存在但未有持久结果；等待并查询，不另开相同请求绕过幂等记录 |
| `WORKSPACE_ADVISORY_RESULT_UNKNOWN` | 已超过该 attempt 保存的截止且仍无持久结果，交给负责人接管；不能推断没有调用或没有费用 |
| `WORKSPACE_ADVISORY_ATTEMPT_FAILED:*` | 查看保留回执和公开错误码，处理来源、模型配置或输出问题；同一事件不自动重试 |
| 输入、授权、运行配置改变或有效期已过 | 旧候选不能继续准入；按当前来源和职责重新修订 |

**持久截止只决定重复请求返回 `IN_PROGRESS` 还是 `UNKNOWN`，不是自动重试租约。** 新 attempt v2 保存与本轮时限一致的截止；旧 v1 已保存的 5 分钟截止按原值读取，不重写历史。执行中的父进程负责停止其 HTTP 子进程；服务进程若丢失，不存在恢复后自动重新派发的机制。晚到结果仍须通过当前授权、来源和有效期检查，旧的未知费用预留不会因时间过去而清零。

失败或未知需要重新生成时，由当前有权负责人在既有流程中拒绝本次变更并说明理由，再以新事件修订，绑定当前来源和 `revises_event_id`。已形成变更组的，使用组拒绝流程；尚未形成组的，处理对应待定事件。不得删除 attempt、改写旧回执或复用旧批准来获得重试。修订意味着一次新的、可能计费的候选运行；未知的旧费用仍需保留并核对。

普通变更详情的 `advisory_attempt` 只读呈现持久尝试：`IN_PROGRESS / RESULT_UNKNOWN / FAILED / COMPLETE`、原截止、单轮费用预留、已观测或未知的用量，以及受限请求 ID 与派发状态。它不返回候选正文、完整请求、价格合同或原始错误文本。`COMPLETE` 只表示候选运行已有持久结果，不替代独立准入、批准或 Apply；没有回执不能推断费用为零。

来源恢复组使用同一个尝试记录和投影。首次候选失败、尚未形成正式组时，可按已知的组 ID 读取 `GET /api/workspace/source-readmission-groups/{group_id}/attempt`。界面保留原尝试 ID 并提供只读核对，不把失败尝试伪装成可批准恢复组。不存在的尝试返回 404；查询不预留、不派发、不自动重试。

## 实现入口

- [service.py](../src/orgrebase/workspace/service.py)：真实模式配置与普通预览的两阶段准入。
- [source_readmission.py](../src/orgrebase/workspace/source_readmission.py)、[preview_execution.py](../src/orgrebase/workspace/preview_execution.py)：多来源预览和共用的持久 attempt。
- [advisory.py](../src/orgrebase/workspace/advisory.py)：领域范围、预算、候选生成及独立验证。
- [openai_responses.py](../src/orgrebase/workspace/openai_responses.py)、[models.py](../src/orgrebase/workspace/models.py)：原生协议、V2 合同与观察语义。
- [openai_http_worker.py](../src/orgrebase/workspace/openai_http_worker.py)：唯一 HTTP 实现与受控进程入口；凭据经私有 stdin 传入，不进入子进程命令或继承的部署环境。
- [model_budget.py](../src/orgrebase/workspace/model_budget.py)：价格合同、每次完整预览的最高费用及确定性取整。
