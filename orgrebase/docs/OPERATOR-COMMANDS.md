# 运行命令

生产配置见 [认证部署](AUTHENTICATED-DEPLOYMENT.md)，数据库准备和恢复见 [状态存储迁移](STATE-STORE-MIGRATIONS.md)。同一应用工厂供 HTTP 服务和后台只读接收使用。

```sh
orgrebase serve --host 127.0.0.1 --port 8081
orgrebase workspace state --url https://orgrebase.example
orgrebase workspace register --url https://orgrebase.example --input /private/events/observed-change.json
orgrebase workspace preview --url https://orgrebase.example --event-id observed-1
orgrebase workspace approve --url https://orgrebase.example --event-id observed-1 --digest sha256:EXACT_PREVIEW_DIGEST
orgrebase workspace reject --url https://orgrebase.example --event-id observed-1 --reason "来源仍需负责人核对"
orgrebase workspace apply --url https://orgrebase.example --event-id observed-1 --digest sha256:EXACT_APPROVAL_DIGEST
```

命令从 `ORGREBASE_ACCESS_TOKEN` 读取短时 access token，也可通过 `--token-variable` 指定另一个环境变量名。审批由实际签名身份和服务端成员配置确定负责人；执行服务使用自己的身份。上例中的摘要占位符必须替换为前一步返回并审核的精确摘要。

多工作区部署中，为同一变更的各步指定同一个 `--workspace`，例如 `orgrebase workspace state --url https://orgrebase.example --workspace renewals`。该参数对登记、预演、批准、拒绝和应用同样有效，通过 `X-OrgRebase-Workspace` 请求头选择现有工作区；它不授予成员或执行权限。省略时沿用服务器配置的默认工作区，不会在 CLI 中强制改选名为 `default` 的工作区。错误的工作区 ID 在发送前拒绝，未授权或未知的工作区由服务器拒绝。

批准与拒绝是不同决定。已拒绝的事件不能继续应用已有批准；修订后登记新的事件，再从预演和批准开始。

CLI 只封装同一 HTTP 命令，不直接修改数据库、不自报负责人。远程地址要求 HTTPS，HTTP 仅允许本机回环地址；拒绝重定向，避免把凭据转发到另一个服务。正式浏览器使用服务端 OIDC Code/PKCE 会话，部署配置见 [认证部署](AUTHENTICATED-DEPLOYMENT.md)。本地真实 HTTPS 协议验证不代替客户 IdP 注册和撤销联调。

其他入口：

| 命令 | 用途 |
|---|---|
| `orgrebase source-sync --help` | [负责人确认后的 Dataverse 接收](DATAVERSE-SOURCE-ONBOARDING.md)，有界分页、当前点读与持久游标 |
| `orgrebase dataverse-target --help` | [目标元数据资格与管理员建表材料](DATAVERSE-TARGET-OPERATIONS.md) |
| `orgrebase recovery-inventory --help` | [恢复库与外部回执双向盘点](RECOVERY-EFFECT-INVENTORY.md) |
| `orgrebase database --help` | 一套数据库备份、恢复隔离和资格检查 |
| `orgrebase audit --help` | [独立审计检查点](AUDIT-CHECKPOINTS.md)，操作员签署、只读验证 |
| `orgrebase business-report --help` | [配对业务观测](BUSINESS-OBSERVATIONS.md)，完整成本与失败分母 |

旧演示命令仍可复验原有受控本地证据；它们不提供生产身份或外部写回资格。当前唯一正式效果状态机见 [提交门](COMMIT-GATEWAY.md)。

当前观察与数据退出见 [企业运维](OPERATIONS-AND-EXIT.md)，暂停中的工作如何升级见 [运行兼容与传输协议](RUNTIME-UPGRADE-AND-WIRE.md)。
