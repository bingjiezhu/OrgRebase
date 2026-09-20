# 受控本地运维包络

> 本文保留早期受控本地运维切片的证据范围，不代表后续企业接入实现清单。当前运行机制见 [企业运维观察与数据退出](OPERATIONS-AND-EXIT.md)、[状态存储迁移](STATE-STORE-MIGRATIONS.md) 和 [源接入](DATAVERSE-SOURCE-ONBOARDING.md)。原切片的限制与测量不因新实现而追溯升级。

该早期切片交付的是一个可重复验证的单机 MVP。这个包络的目的，是把“接口真的跨进程/网络、
证据真的可查询、静止 SQLite 副本的备份/恢复原语已执行”与“业务运行可中断恢复、已经接入客户系统、
已经满足生产 SLA”严格分开。

## 能证明什么

- Source 与 Dependency Tool 通过 `127.0.0.1` 上的真实 HTTP/JSON 调用，而不是函数内伪装连接器；
- Source 使用 bearer token、严格响应 Schema、内容 ETag 和 `304 Not Modified`；Tool 校验请求摘要、同一
  `run_id`、任务、目标、图摘要以及 `target_writes=0`；
- traces、logs、metrics 分别通过 OTLP/HTTP JSON 入口写入独立 SQLite 后端；
- 可按 `run_id`、trace、任务、Skill 包、收据、证据等级、状态及时间窗查询；
- 告警可确定性识别缺失终态、链路缺层、Tool 失败、Skill 隔离、批准/Apply 不一致、候选越权写入和过期
  attempt 结果；
- SQLite 备份在新文件中恢复，并比较全部逻辑表、对象数、制品数和事件链头；这只证明静止副本的
  逻辑备份/恢复原语，不证明 AgentTeams 中途恢复、Workspace Resume 或线上灾备；
- 容量输出只报告本机样本、耗时、p50/p95 和吞吐观察，字段明确禁止 `SLA_MET` 与
  `PRODUCTION_READY` 声明；
- CycloneDX 1.5 SBOM 与 `uv.lock`、`pyproject.toml`、wheel/source archive 的 SHA-256 绑定。

## 不能证明什么

CRM、CPQ、ERP、CLM 和企业知识库仍为 `NOT_RUN`；多租户隔离、TLS、在线迁移、高可用、跨地域灾备、
合同 SLA/SLO 和漏洞不存在性也没有被证明。受控本地 HTTP 的证据等级是
`CONTROLLED_LOCAL_REAL_HTTP`，运维观察是 `OBSERVED_CONTROLLED_LOCAL`，二者都不能升级成
`LIVE_ENTERPRISE`。

## 权威边界

| 组件 | 持有什么 | 明确不持有什么 |
|---|---|---|
| StateStore | 规范对象、批准、事件链与制品 | AgentTeams 调度事实、遥测查询结果 |
| AgentTeams adapter | 项目/任务/attempt 的调度事实；当前 retained 证据是受控本地 native responses | 业务对象当前版本、Apply 权限；外部 live Worker 仍为 `NOT_RUN` |
| Skill package | 确定的输入输出契约与候选结果 | 批准权、规范写权限 |
| Source / Tool connector | 连接器读取结果与调用收据；当前只验证 loopback synthetic 服务 | 规范写权限、真实企业系统状态 |
| TelemetryStore | 可检索的运行投影、拒收摘要 | 业务真相、原始秘密、原始 prompt/output |

任何冲突都不能用一条遥测记录覆盖 StateStore，也不能把 AT 的 `submitted` 当成业务验收。本切片已验证
拒绝、重派和 late-attempt fencing；同-run Workspace Compensation 与 durable Resume 仍为 `NOT_RUN`，
不能只因契约中定义了语义就声称已经执行。

## OTLP 公共字段

三类 OTLP payload 的 resource 必须携带 `service.name`、`service.version` 和环境，且其运行投影必须可按
组织、根 `run_id` 与证据等级关联。trace/log 的层记录携带状态与时间；metric data point 携带时间戳与
层计数。阶段 run 与 OAC roots、AT project/task/attempt/delegation、Skill name/version/package/invocation、
Tool request/result、批准、effect、outcome、rollback、错误码、重试/重派数和候选写入数，只在该信号所描述的
层确实存在时必填；候选链没有 Approval/Apply 时不得伪造对应字段。只传标识和摘要；
`authorization`、`token`、`secret`、原始 prompt/output/source content 和隐私 canary 会 fail closed。
接收失败只保留 payload 摘要、原因和时间，不保留拒收原文。

## 留存、查询与告警

- indexed telemetry：本地默认 30 天；
- rejected diagnostics：本地默认 7 天，仅保留摘要与原因；
- canonical evidence：不由遥测清理器删除；
- 查询和清理各自产生内容寻址收据；
- 最低严重告警包括：缺终态、同 run 链缺层、Tool 失败、Skill quarantine、未批准 Apply、候选写入、
  fenced late result、接收失败和恢复演练失败。

告警只说明“必须停止晋级或人工检查”，不会自动改变规范状态。人工处置后，应通过新批准、重派或补偿事件
形成新的不可变事实。

## 部署、容量与恢复

配置基线是 [`configs/deployment/controlled-local.json`](../configs/deployment/controlled-local.json)。Source/
Tool 和 OTLP receiver 仅绑定 loopback，端口动态分配，令牌只在进程内有效。`/healthz` 与 `/readyz`
证明本地服务线程可接收请求，不代表下游企业系统健康。

容量测试是 25 次有界调用的 smoke observation；它给出环境事实及观测吞吐，但不设置生产通过阈值。恢复演练
在静止的 SQLite 副本上执行，所以只能记录本次本地恢复耗时观察，不能把它称为生产 RTO，也不能推出线上 RPO/RTO。生产容量建模、压力/浸泡测试、
多副本一致性和异地切换保留为下一阶段的部署 Spec，而不是本 MVP 的隐藏承诺。

## SBOM 与供应链边界

`scripts/generate_sbom.py` 从锁文件生成确定性的 CycloneDX 1.5 清单，并绑定构建制品摘要。同一锁文件与同一
制品必须生成相同字节。SBOM 是依赖清单和来源证明，不是安全扫描结果，也不能证明没有 CVE。真实发布还需
签名、来源 attestation、漏洞扫描和撤回流程。
