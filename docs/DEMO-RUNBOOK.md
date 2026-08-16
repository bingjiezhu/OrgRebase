# 3-minute demo runbook

## 0:00–0:30 · 问题

打开 Change Console。说明一个发布日期改动不应触发全公司重做，也不能因为搜索
不到依赖就断言“不受影响”。点击 **Reset** 确认起始状态。

## 0:30–1:15 · Preview

点击 **Preview impact**。展示精确结果：

- 2 个 `AFFECTED_HARD`：Sales Quote A、Support Launch Document B；
- 2 个 `UNAFFECTED_WITHIN_DECLARED_BOUNDARY`：Legal、Finance；
- 1 个 `UNKNOWN`：Partner Brief，没有完整历史依赖证据；
- 1 个 `REQUALIFICATION_REQUIRED`：Enterprise Launch Readiness Skill。

指出每项都带可重算 ImpactCertificate；DependencyManifest 清单槽位与图边精确对应。
展示 VMRC：删掉必要 `REBUILD` 或额外加入无关 `REBUILD` 都验证失败。

## 1:15–2:15 · Apply

点击 **Approve & apply**。解释本地显式审批只用于确定性演示；生产将接真实 Matrix/
审批系统。展示：Claim v7 → v8；两项工作产生 v2；Legal/Finance 保持 v1；Partner
进入人工复核；不同角色得到不同 ContextManifest，受限 Legal 原文没有泄露。随后
展示控制面真实 Git commit；补偿时权威 Claim v8 不倒退，下游产生
`ROLLED_BACK_PENDING_REBASE` 的 v3，并用真实 Git revert 消除外部效果。

## 2:15–2:45 · Skill 与 AgentTeams

先展示 `OrchestrationPlan`：五个身份被编译成有前置关系、最小上下文、允许输出与失败
语义的任务 DAG，所有任务绑定同一 ChangeSet/Preview/RevisionLock；随后展示
`CoordinationReceipt=PASS`。再展示真实 AgentTeams v1.2.2 的五个 Worker、Matrix 事件与
四个 Vertex provider request。
然后展示 proof-carrying delegation：Leader 先在 Matrix 事件中承诺 Plan digest，四个真实
候选再带回精确 task/input 绑定。候选控制面回执显示四个全部为
`ADVISORY_ACCEPTED`，但零目标写入。再展示同一
冻结评测下 `v1.2 FAIL → v1.3 PASS → CANARY`；通过并不等于全量 ACTIVE。

## 2:45–3:00 · Receipt

展示完整 Receipt、事件摘要链、跨 SQLite/Git 补偿 saga 与证据边界：本地确定性路径
通过；企业数据是合成的；AgentTeams 集群 E2E 是 `LIVE_AGENTTEAMS`；真实人类 Matrix
审批和企业连接器仍是 `NOT_RUN`。最后运行 manifest verifier 与 release-facts drift check。

## CLI fallback

```bash
make demo
uv run orgrebase verify evidence/latest/rebase-receipt.json
```

预期摘要：`2 affected · 2 bounded unaffected · 1 unknown`；Skill 每次输出绑定 Preview、
对象、reason 与合同 digest 的 `SkillActionCandidate`，1.3 最终为 CANARY，
未授权披露与错误失效均为 0。
