# OrgRebase 从初赛到 OAC 受治理演化基座

**文档状态**：历史架构收敛记录

**历史时点运行权威**：归档 Spec 060；当时的 OAC 合成参考闭环为归档 Spec 039。

**历史时点材料边界**：归档 Spec 061；当前事实与主张边界统一见 [L0 产品真相](SYSTEM-MAP.md)。
**完成声明**：当前 Golden Quote 与 OAC reference journey 是两条分离证据泳道；本文不把它们拼成同一 OAC-native Quote run，也不把受控合成证据写成生产系统或任意企业泛化证明。

## 一句话结论

初赛版证明了：

> 企业成果可以像软件构建产物一样，在事实变化后被精确定位、审批、重建、恢复和审计。

当前收敛版要在不改变这个产品内核的前提下，再证明：

> 企业事实和目标可以通过一套 OAC 契约形成不同但都合规的工作组织；执行成功不等于业务结果合格；有效经验只能作为候选，经独立治理权限批准精确字节后，才能成为新的企业 Source，且可回滚。

这不是“再做一个 Agent”，而是定义 Agent、人、知识、工具、权限、工作顺序和证据如何组成一个可替换、可验证、可演化的企业工作系统。

## 历史架构图与当前系统图

- [初赛 Reference Runtime 架构图](diagrams/preliminary-reference-runtime.html)
- [历史 OAC 合成参考演化图](diagrams/oac-governed-evolution-loop.html)
- [当前产品、控制面与证据泳道系统图](SYSTEM-MAP.md)

两张 HTML 图只解释历史演化，不再承担当前运行权威。当前产品主线、Golden Quote、OAC 门控适配及证据边界统一以 [L0 产品真相](SYSTEM-MAP.md) 为准。

## 初赛版的责任树

```text
OrgRebase preliminary product
├── Enterprise facts
│   └── fixed Northstar / Acme Quote fixtures
├── Candidate plane
│   └── Product / Legal / Finance / GTM local candidates
├── Formation authority
│   ├── Reference Monitor: records facts actually read
│   ├── WorkTrace and field lineage
│   └── Quote v1 + dependency graph
├── Change-control authority
│   ├── Impact: AFFECTED / KEEP / UNKNOWN
│   ├── exact VMRC
│   └── digest-bound owner approval
├── Canonical write authority
│   └── SQLite transaction: Quote v2 / v3 + pointer + event
└── Recovery and audit
    └── restart / export / rollback / evidence pack
```

这个版本的关键不是 Agent 数量，而是三个不可合并的权限：

1. Agent 和领域 Provider 只有候选权。
2. 影响分析、VMRC 和审批决定哪些旧成果仍可信。
3. 确定性 Writer 是唯一 canonical write authority。

## 当前收敛版的责任树

```text
OrgRebase + independent OAC standard
├── Standard authority: oac-spec
│   ├── strict Source / Demand / Plan / Outcome resource kinds
│   ├── RFC 8785 detached digests and public CLI
│   └── Schema / Kind Registry / TCK / reason codes
├── Enterprise source authority: OrgRebase intake
│   ├── EnterpriseSeedProfile and exact source bytes
│   ├── immutable profile migration r1 → r2
│   └── declared authority, not authenticated enterprise IAM
├── Organization formation
│   ├── same admitted Source + Demand
│   ├── BASE and SPLIT: different topology, same obligations
│   └── public OAC verification and runtime admission
├── Replaceable Runtime authority
│   ├── topology-driven zero-effect handlers
│   ├── durable ExecutionReceipt and idempotent restart replay
│   └── cannot issue Outcome or mutate Source
├── Independent Outcome authority
│   ├── controlled observation producer seals actual business facts
│   ├── oracle evaluates an existing observation
│   └── ACCEPT / REJECT is independent from COMPLETED
├── Candidate-only evolution
│   ├── Procedure candidate keeps obligations/evidence/authority gates
│   ├── no complete DAG, case id or Plan digest
│   └── rejected Outcome remains counterevidence
├── Source governance authority
│   ├── proposal binds prepared Profile/Snapshot bytes and allowed delta
│   ├── distinct allowlisted governance identity approves exact proposal
│   └── scripted evidence != human review evidence
└── Canonical successor and rollback
    ├── immutable Profile / Snapshot / Source successor
    ├── pointer r1 → r2 → r1; history retained
    └── content-addressed evidence index and independent verification
```

## 端到端闭环

| 阶段 | 人话解释 | 权威制品 | 谁不得自证 |
|---|---|---|---|
| Source | 企业当前认可的事实、能力、责任和边界 | Profile, Snapshot, SourceAdmissionReceipt | 模型和 Runtime 不能自行发明企业事实 |
| Demand | 这次企业希望完成什么 | OrganizationalDemand | 编译器不能静默改写目标 |
| Plan | 哪些人/Agent 参与，各自负责什么，怎样交接 | OrganizationPlan + PlanCertificate | 编译器不能给自己发合格证 |
| Execution | 工作是否按计划执行完，产生了什么证据 | ExecutionReceipt | Runtime 只能证明执行事实，不能决定业务结果 |
| Outcome | 完成的工作在业务上是否可接受 | OutcomeObservation + OutcomeCertificate | Runtime 不能生产自己的结果真值 |
| Evolution | 哪些经验值得成为下次可复用能力 | Procedure candidate + EvolutionProposal | 候选生成者和 Runtime Owner 不能自审批 |
| Successor | 企业正式承认的新版本，且能回到旧版本 | immutable Source/Profile/Snapshot + rollback receipt | `promote()` 不能在批准后临时生成其他字节 |

## 与初赛相同的思想

- 企业成果是版本化构建产物，不是聊天记录。
- 候选生成权与 canonical write authority 分离。
- 真实读取和精确输入根是证据基础。
- `UNKNOWN` 不能被静默变成成功。
- 新版本是追加而不是覆盖；回滚是指针移动而不是删除历史。

## 真正新增的能力

| 能力 | 初赛 | 当前收敛版 | 是否只是复杂化 |
|---|---|---|---|
| 企业输入 | 固定 Northstar fixture | Profile + exact Source bytes + admission | 否；关闭“声明的数据与真正运行数据不一致” |
| 组织方案 | 固定候选池中选组合 | 同一契约允许 BASE/SPLIT 两种拓扑 | 否；验证“标准约束合法集合”而不是固定 Agent 图 |
| 执行与结果 | 主要由完成状态表示 | `Plan ACCEPT → Execution COMPLETED → Outcome REJECT` | 否；关闭执行者自证业务成功 |
| 经验沉淀 | 固定 SkillFoundry 样例 | 不固化 DAG 的 Procedure candidate + 反例 | 否；保留下次动态组织空间 |
| 演化治理 | 业务 Quote 版本更新 | 批准精确 successor 字节，Source r1→r2→r1 | 否；关闭未经治理的自我修改 |
| 模型/运行时 | 当前实现与业务逻辑耦合 | OAC 契约是产品真相，Runtime 是可替换 adapter | 否；这是与 Codex/Claude 等 Agent 本体的核心区别 |

## 当前可辩护的创新点

不把以下单点写成创新：多 Agent、动态图、MCP、Agent 通信、Skill 文件、Trace Store、事件总线、工作流 Runtime。它们都已有先行工作。

当前最强且边界清晰的主张是：

> 用厂商中立契约定义企业 Source、Demand、合规组织、执行证据、独立结果与演化提案的关系；Agent 和 Runtime 可替换，但不能获得企业真相的自证权。从多种有效拓扑中提取的经验只是候选；独立治理权限先批准精确新字节，再发布不可变 successor，并保留反例、历史和回滚。

它是一项**受限组合创新的参考实现证据**，不是“世界首创”证明。

## 验证口径

最大可辩护口径必须保持为：

`ONE_GOVERNED_EVOLUTION_REFERENCE_MVP_WITH_PRELIMINARY_REGRESSION`

对非技术评委的人话版：

> 一个完整 OAC 合成参考闭环，加一个初赛 Quote 精确非回归闭环。

这不等于：

- 两个企业已完成同一 OAC 全生命周期；
- 真实企业数据已验证；
- 人类审批已运行；
- 只提供数据就能自动适配任意企业；
- 企业生产所需的 IAM、多租户、高并发、HA 和真实外部副作用已完成。

## 文件夹架构的收敛

```text
orgrebase/
├── src/orgrebase/              # 产品核心与 Workspace Reference Runtime
├── tests/                      # 行为、权限、攻击、回归门
├── schemas/                    # OrgRebase 本地契约；不反向定义 OAC
├── configs/oac/                # Runtime 自己的二次准入策略
├── evidence/
│   ├── oac-evolution/latest/  # 当前主闭环证据
│   └── product-closure/       # 初赛 Quote 产品精确回归
├── docs/
│   ├── SYSTEM-MAP.md         # 当前唯一人工可读产品真相（L0）
│   ├── README.md             # 渐进披露索引（L1/L2）
│   └── diagrams/             # 跨版本与当前架构可视化
└── submission/                # 发布/比赛材料，不是运行时权威

../oac-spec/
├── standard/                   # 规范文本
├── schemas/                    # 公开 Kind 与 reason-code registry
├── src/oac/                    # 非规范参考库与公开 CLI
├── profiles/                   # 供应商变化 Profile
├── tck/                        # 标准符合性用例
└── specs/009-...               # 当前最小 proof-carrying evolution Profile
```

已归档的未准入研究只能在「展望」中出现，不得进入当前 MVP 架构图、完成度、测试统计或创新验证主张。

## 面向 PPT 与 Demo 的表达顺序

1. **企业变化发生**：旧成果为什么很快过时？
2. **初赛已解决**：Quote v1→v2→v3，只重建受影响部分，可恢复、可审计。
3. **上一版还不够**：固定企业、固定候选组织、执行成功与业务结果未分开。
4. **OAC 的方法**：企业用契约提供 Source 和 Demand，系统允许多种合规组织。
5. **最强反例**：所有步骤执行完，独立业务结果仍然拒绝。
6. **演化不是自我修改**：只产生不固化 DAG 的候选；独立治理权限先批准精确字节。
7. **可回滚**：Source r1→r2→r1，历史不删除。
8. **证据边界**：合成受控参考 MVP，不是生产或任意企业泛化证明。

技术哈希、Schema 列表、全部 Trace 和测试数量属于「证据层」，应放在附录或第二层展开中；主叙事始终围绕一个问题：**企业如何把一次变化，变成一次可证明、可沉淀、可回滚的能力升级。**
