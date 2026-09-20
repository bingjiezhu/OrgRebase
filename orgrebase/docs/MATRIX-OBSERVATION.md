# WebUI 与 Element 的同任务观察

OrgRebase 的 WebUI 负责业务输入、独立校验、精确审批和成果状态。Element 展示同一次运行的简要动态。两者使用同一个运行标识，业务状态仍由 OrgRebase 控制面管理。

此接入把服务端已验证的运行快照发布到真实 Matrix 房间，并读取服务器保存的消息确认发送结果。消息由一个普通 OrgRebase 服务账号发布，不假扮 Product、Finance 或 Reviewer。Element 观察成功不代表 Worker 从 Matrix 收到任务，也不代表分布式 AgentTeams 调度或生产验收完成。

## 运行方式

1. 在 WebUI 完成任务初始形成，产生可核对的原生 AgentTeams 执行事实。
2. 在“真实协作进度”中发布当前观察。浏览器只能提交当前 actor 和 run ID，不能指定房间、接收者、URL 或消息正文。
3. 服务端重新读取业务状态，形成最小快照，校验配置范围和 Matrix 身份，创建或复用该任务的私有房间。
4. 服务端发送消息，并核对服务器返回的 event ID、sender、房间和完整内容。
5. WebUI 展示对应 Element 房间入口。后续报价版本发生变化时，旧观察显示待同步；再次发布后产生新观察消息。

房间仅允许服务账号发布消息。指定 viewer 可以加入和阅读，不能发送消息、修改状态或邀请成员。审批仍在 WebUI 完成。没有聊天室命令监听器，也没有第二套报价或任务状态库。

消息正文为已知角色和任务状态提供中文说明，同时保留原始状态码与完整任务编号。不同重试仍有各自的任务编号，未知状态按原值呈现。结构化快照不随文案翻译改变；正文变化会产生新的内容绑定消息，重复发布同一内容继续复用相同事务标识。历史消息和回执不作改写。

## 部署配置

需要一个支持 Matrix Client-Server API 的 homeserver 和一个 Element Web 客户端。两者可以独立部署，不要求 Kubernetes。若使用 AgentTeams 的部署服务，可复用其 Matrix/Element，但应创建专用的普通 observer、viewer 账号，不能使用 Application Service 总令牌、管理员账号或 Worker 凭据代替。

参考 `configs/workspace/matrix-observer.example.json`。示例令牌 `PLACEHOLDER` 不能用于连接。真实配置保存在仓库之外，并设置为仅当前用户可读写的普通文件；符号链接和组/其他用户可访问的权限会被拒绝。

当前本机部署使用外层 `.runtime/element/observer.json`，不纳入源码交付。从本仓库目录启动时，可设置：

```bash
export ORGREBASE_MATRIX_OBSERVER_CONFIG="$PWD/../.runtime/element/observer.json"
export ORGREBASE_PUBLIC_WORKSPACE_URL="http://127.0.0.1:8015"
chmod 600 "$ORGREBASE_MATRIX_OBSERVER_CONFIG"
```

`ORGREBASE_PUBLIC_WORKSPACE_URL` 应是当前 WebUI 对用户可访问的固定地址；端口按实际启动参数填写。它不能由浏览器请求决定，也不包含查询参数、页面 fragment 或登录凭据。Element 与 homeserver URL 同样只接受 HTTPS 或 loopback HTTP。令牌只放在请求的 Authorization 头，不写入链接、回执、日志正文或配置 repr。

| 字段 | 用途 |
|---|---|
| `homeserver_url` | Matrix Client-Server API 的部署基址，可包含部署路径前缀 |
| `element_url` | Element Web 基址，不含 `#/room/...` |
| `access_token` | 普通 observer 账号令牌，仅存在私有配置中 |
| `user_id` | observer 的完整 Matrix 用户 ID，必须与 `whoami` 一致 |
| `allowed_workspace_ids` | 必填、非空、无重复的工作区允许集合 |
| `viewer_user_id` | 可选的普通观察者账号，仅邀请这一账号 |

工作区标识由组织 ID 和 Workspace ID 组成，例如 `org:evergreen-industries/default`。若原始 ID 含 `/` 等分隔字符，分别进行 URL 百分号编码后再用 `/` 连接，防止两个不同组织/工作区组合得到同一标识。禁止用空集合、通配符或省略字段开放全部租户。同一个 viewer 被配置到多个允许的工作区，意味着部署方明确授权该 viewer 阅读这些工作区的运行摘要。

允许集合限制后续发布，不自动撤销已经存在的 Matrix 房间成员权限。需要收回历史房间访问权时，应由部署方同步处理对应 viewer 的房间成员关系；本接入不暗中维护第二套人员权限同步系统。

每个工作区与 run 对应一个稳定房间 alias。创建返回超时或其他不明确结果时，适配器先解析该 alias，避免重试创建重复房间。房间绑定组织/工作区、run、原生 project、服务账号和部署地址。更换这些绑定不自动迁移旧房间，也不改写业务运行。

## 数据与权限

公开消息只包括：

- run ID、原生 project ID、当前阶段及来源摘要。
- 初始形成阶段的 AgentTeams 操作次数。
- task ID、领域和任务状态。
- 当前成果引用与版本，以及 WebUI 地址。

不发布报价金额、客户合同、原始 prompt、模型完整输出、密钥或业务数据库内容。初始形成的操作次数不会被描述成每个后续报价版本重新执行的次数。

观察房间采用邀请制、禁用访客、禁止联邦。服务账号与配置中的 viewer 之外出现额外已加入、受邀或敲门成员时，发布被拒绝。普通 viewer 没有消息与状态写入权。每次发布都会重新核对房间成员、权限及绑定；已有房间不符合约束时明确失败，不静默降低要求。

认证请求会在取得同运行文件锁后、创建房间前和发送消息前重新核对当前身份与工作区权限。等待期间发生会话过期或撤权时，返回原本的 401/403，停止后续外部发布；不会将授权拒绝当作 Matrix 网络故障。

这是一份发布时的校验，不是持续健康或权限监控。Matrix 管理员仍管理部署和服务器数据；需要按部署环境配置备份、留存和访问控制。此接入未提供端到端加密消息，不能把私有房间等同于 E2EE。

## 状态与失败

| 状态 | 含义 |
|---|---|
| `NOT_CONFIGURED` | 未配置观察接入 |
| `WAITING_FOR_RUN` | 当前没有符合要求的已观察原生运行 |
| `NOT_PUBLISHED` | 当前工作区/run 尚未发布观察 |
| `OBSERVED` | 最近一次消息发送和服务器读回核对成功，来源仍匹配当前状态 |
| `STALE` | 已有观察，但当前业务快照发生变化，需要再次同步 |
| `UNAVAILABLE` | 发布失败或回执不可信，不能显示为当前成功连接 |

GET 只读取已经保存的观察回执和传入的可信业务快照，不创建文件、不读取 access token 配置、不联网探测。因此 `OBSERVED` 表示最近一次已核验的消息，不是实时服务健康承诺。配置后来失效或服务不可达，会在下一次真实发布尝试中暴露。

配置不可用、Matrix 通信或读回校验失败，会把相应观察记录标为不可用。前置配置/URL 失败使用回执摘要进行条件更新：如果另一个并发发布已经成功，较旧失败不会覆盖新成功。非法 actor、错误 run 和工作区范围拒绝不会发布消息。通信失败不回滚或修改业务成果。

身份或权限拒绝不会伪造观察回执。WebUI 单独提示本次请求未完成，并通过统一状态刷新读取最后经核验的记录；用户仍有读取权时，可以看到先前的真实观察及本次失败提示。POST 的迟到响应不会直接覆盖当前工作区状态。

私有配置旁的 `observations/` 仅保存房间绑定和当前观察回执。文件使用原子替换，发布使用同运行文件锁。它们是可重建的展示证据，不是业务数据源。相同快照采用稳定 Matrix transaction ID，重复同步不应生成重复消息。

## 验证

```bash
uv run pytest tests/workspace/test_matrix_observation.py tests/workspace/test_matrix_workspace.py
```

这些测试覆盖 HTTP 协议、身份、工作区隔离、房间受众与权限、重试、错误读回、失败状态、并发结果保护和 GET 无副作用。协议夹具不代表真实 homeserver 已经部署；实际 Synapse/Tuwunel、Element、viewer 的验收必须另行保留对应运行结果。

AgentTeams 原生执行的源码版本与锁文件继续由原有机制管理。本观察适配器不修改 Worker 编排，不把升级版本号当作新的运行证据，也不扩大现有的受控本地验证边界。

### 已验证的本机组合（2026-09-13）

实际运行使用 Synapse 1.160.0、Element Web 1.12.27，以及锁定到
`223ddc2b8073e4c8b93bcbb15e1d717f196c04d9` 的 AgentTeams v1.2.3 源码。
本机服务与安装缓存留在私有运行目录，没有加入产品依赖或改变默认部署方式。

本次控制面运行完成报价 `v1 → v2 → v3`，初始形成包含 40 个原生动作、7 个本机
Worker / Reviewer 进程和 2 次本地模型调用。随后从真实 WebUI 发布同一 run 的观察，
在 Element 以普通 viewer 登录并读到该消息。独立 HTTP 验收确认：

- 同一快照重复同步及故障恢复后重试，返回同一 Matrix event ID。
- 错误 actor 返回 403，过期 run 返回 409，浏览器指定房间返回 422。
- 配置暂时不可用时返回 503；配置恢复后，GET 不会让旧成功重新出现，重新同步才能恢复。
- viewer 读取成功；发消息、修改房间权限及访问管理员接口均返回 403。
- 同步前后业务数据库的只读逻辑导出摘要一致，报价和审批数据没有被通信操作改写。

这些结果使用受控合成企业样例。真实 Matrix 仅承载运行观察；原生业务运行的 Worker
传输仍是受控本地 HTTP 桩，不能将两者合称为真实分布式 AgentTeams 运行。
本机 HTTP 仅监听回环地址，不构成远程部署、安全认证或客户价值验收。

上游参考：[AgentTeams v1.2.3](https://github.com/agentscope-ai/AgentTeams/releases/tag/v1.2.3)、
[AgentTeams 本机部署](https://github.com/agentscope-ai/AgentTeams/blob/v1.2.3/docs/usage/deployment/local.md)、
[Element Web](https://github.com/element-hq/element-web)、
[Synapse](https://github.com/element-hq/synapse)。
