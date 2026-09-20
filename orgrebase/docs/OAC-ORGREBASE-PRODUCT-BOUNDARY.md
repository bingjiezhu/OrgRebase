# OAC 与 OrgRebase：从企业契约到可运行产品

> 本文是 [L0 产品真相](SYSTEM-MAP.md) 的 OAC / OrgRebase 边界 L1；如有不一致，以 L0 和当前机器事实为准。

## 结论先行

OAC 不是 OrgRebase 的新名字，OrgRebase 也不是 OAC 标准本身。

> **OAC 是“组织级 Agent 契约标准草案”；OrgRebase 是实现该契约边界、并负责企业工作持续演化的产品与唯一确定性控制面。**

Enterprise Quote Profile 是当前一个可验证的应用切片，不是 OAC 的全部。AgentTeams、模型、Tool 和
Skill 都可替换，且只能产生候选事实；它们不能自我授权、代替人审或直写企业规范状态。

<!-- CURRENT-OAC-VALIDATION-STATUS:START -->

| 当前状态 | 已验证 | 尚未验证 |
| --- | --- | --- |
| `VALIDATED_CONTROLLED_LOCAL` | 五类企业材料的确定性校验、Unknown/Gap 保留、负责人准入与精确激活绑定；当前 Golden 在同一 `run_id` 中消费该绑定，形成任务、最小上下文和 AgentTeams 执行计划，并完成后续业务闭环 | 真实企业人审、真实企业数据与连接器、员工 UAT、任意流程零配置适配、分布式 AgentTeams 或生产 SLA/HA/DR |

> 当前 Golden 的公开验证包含 101 条内容寻址记录。它证明 OAC 激活绑定确实进入了同一业务运行；它仍不等于真实企业验证或生产就绪。五条独立证据链的 entries 与 `run_id` 不相加、不伪合并。

<!-- CURRENT-OAC-VALIDATION-STATUS:END -->

## 1. 一棵责任树，不是两个产品

```text
企业意图、数据与责任人
└─ OAC proposed standard（可移植契约）
   ├─ Source / Demand / authority / obligation / evidence 关系
   ├─ canonical bytes / digest / reason code / lifecycle constraints
   └─ 不包含 Runtime、IAM、任务调度和业务写入
        │ public CLI + exact wire
        ▼
   OAC 企业适配层（OrgRebase 中的部署前产品能力）
   ├─ 五根材料 -> candidate mapping -> Unknown/Gap
   ├─ OAC Source / Demand validation -> 4 秒人工准入
   └─ SourceAdmissionReceipt + OrgRebase parity + immutable capsule
        │ exact capsule/profile/pack binding
        ▼
   OrgRebase 企业工作持续演化引擎（唯一产品主线）
   ├─ AgentTeams / Model / Tool / Skill 候选协作
   ├─ Formation / Impact / Preview / Owner Approval / Selective Rebase
   └─ StateStore / RebaseWorkflow canonical state + evidence + recovery
```

OAC 企业适配层是 OrgRebase 的“部署前准入步骤”，不是第二 Runtime、第二 StateStore、第二 Registry
或第二个业务故事。它只解决一个问题：**企业提供的材料，在什么契约、未知项和人类责任下，才可以安全进入 OrgRebase。**

## 当前已经从“准入”走到“动态执行绑定”

Spec 067/068 将 OAC 的价值从静态契约延伸为可执行的参考链，但仍不让 OAC 成为第二 Runtime：

```text
企业五根材料 / 公开真实流程数据
  -> OAC Intake Agent 候选映射
  -> 确定性校验 + Unknown 保留 + 4 秒负责人门
  -> TaskFormationDecisionReceipt
  -> TaskAgentContextEnvelope
  -> AgentTeamsExecutionPlan
  -> 精确领域任务 + Reviewer
  -> AT + Tool + Skill + OTLP + Candidate
  -> OrgRebase 控制面 / 人工负责人
```

接入 Agent 相当于 AI-FDE 的候选理解层；它可以读取材料、访谈人员并建议映射，但不能把
Unknown 猜成真值。Formation 才从已准入的目标、证据义务、Capability Card 和硬策略中重算
最小团队；Worker 只接收本次任务投影，结果结构化返回 Reviewer / 控制面。

当前有两个不同运行的可执行证据：Quote 运行选中并实际创建四域；residency-FAQ 运行只选中并实际创建
Legal + Product 两域和一个 Reviewer。后者证明方法没有被报价案例写死。

## 2. 当前 Enterprise Quote Profile

企业报价材料被收敛为五个组件根：

| Pack 根 | 企业声明 | 适配和运行用途 |
| --- | --- | --- |
| `DOMAIN` | 业务对象、流程边界、变化语义 | 形成任务与领域投影 |
| `KNOWLEDGE` | 当前/拟议事实、版本与来源 | 形成 exact source bindings |
| `AUTHORITY` | Owner、批准、拒绝与升级路径 | 限定人工准入与业务批准 |
| `CAPABILITY` | Agent / Skill / Tool 能力与允许范围 | 防止 Runtime 自由扩权 |
| `DEPENDENCY` | 跨域依赖、顺序与证据义务 | 支持 Formation 和选择性 Rebase |

完整不等于正确。缺失 Owner、来源、权限、能力或依赖时，只能保留为 `UNKNOWN/BLOCKED` 并停在
`HOLD`，不得由模型补猜。这些字节当前定义的是 **OrgRebase Enterprise Quote Profile**，不是已认证的通用 OAC Profile。

## 3. 企业适配基础真正落地什么

```text
Enterprise Quote Pack
  -> 5 个 candidate-only 语义映射
  -> 确定性 Unknown / Gap 检查
  -> OAC OrganizationSnapshot + OrganizationalDemand
  -> OAC public CLI schema / digest / lifecycle validation
  -> Enterprise Contract Owner 服务端 4 秒门 + exact-digest 决定
  -> OAC SourceAdmissionReceipt
  -> OrgRebase QuoteFormationParityReceipt
  -> OACAdapterCapsule
  -> exact profile / pack / capsule 绑定的新 OrgRebase 运行
```

最小适配基础仅使用 OAC 已有的 Source / Demand / SourceAdmission 公共契约。当前 OAC public compiler/verifier
只支持 SupplierChange，所以 Quote 的 Formation 覆盖关系必须由 OrgRebase-owned
`QuoteFormationParityReceipt` 证明，并且显式声明：

```text
oac_plan_produced=false
oac_plan_certificate_produced=false
oac_runtime_invoked=false
formation_authority=ORGREBASE_CONTROL_PLANE
claim=OAC_SOURCE_DEMAND_ADMITTED_AND_ORGREBASE_FORMATION_PARITY
```

`QuoteFormationParityReceipt` 不是 OAC `PlanCertificate`。用实现侧自签证书替代标准符合性，
会使这项创新失去可信性。

## 4. 五条当前证据链，不能相加或拼接

| 证据面 | 当前状态 | 证明什么 | 不证明什么 |
| --- | --- | --- | --- |
| 当前 Golden 业务闭环 | `VALIDATED_CONTROLLED_LOCAL`，101 条内容寻址记录 | 同 run OAC 激活绑定、员工任务准入、Formation / Context / AT Plan、AgentTeams、Tool、Skill、两次人工批准、选择性 Rebase、终态和完成态重启 | 真实企业 UAT、ROI 或生产 SLA/HA/DR |
| OAC 企业资料适配 | `VALIDATED_CONTROLLED_LOCAL` | 五类材料、Unknown/Gap、负责人准入、SourceAdmission、Formation parity 与不可变激活绑定；缺失责任信息的反例保持 `HOLD` | 任意企业零配置接入、真实企业人员验收或生产连接器 |
| BPI 2019 公开真实流程 | `VALIDATED_CONTROLLED_LOCAL` | 公开真实匿名采购到付款日志的来源绑定、固定投影、128 个任务规则查询与闭世重放 | 报价数据、人工因果真值、企业价值观或 ROI |
| 动态 Formation 拓扑 | `VALIDATED_CONTROLLED_LOCAL` | 不同需求形成不同领域 Agent + Reviewer 拓扑，证明团队不是固定全量队列 | 与 Golden 是同一 run，或 Agent 获得业务写入权 |
| BPI → OAC Agent 候选适配 | `VALIDATED_CONTROLLED_LOCAL` | Agent 可生成受限映射候选；确定性校验保留 Unknown、权限、负责人和审批缺口 | 自动采信 Agent 候选、真实企业采纳或任意流程适配 |

永久规则：`entries` 数不相加，`run_id` 不伪合并，综合查看不等于形成新的同一运行证据。

ProductPath v0.3 是独立的黑盒产品路径与发布防退化门；它检查员工需求入口、范围准入、固定点重放和攻击变体，不是第六条业务证据链。旧 OAC-bound Shadow 只作为历史回归机制保留。

## 5. 与现有互联标准的关系

| 现有层 | 解决的核心问题 | OAC 的边界 |
| --- | --- | --- |
| [GB/Z 185-2026 智能体互联系列](https://openstd.samr.gov.cn/bzgk/std/nd?no=2781) / [ACPs 开源索引](https://github.com/AIP-PUB/ACPs-community/blob/main/acps-specs/README.md) | 中国智能体互联的架构、身份、描述、发现、交互、工具与监控 | GB/Z 是指导性技术文件；当前项目未声称贯标，只预留外部 identity/capability/interaction 坐标 |
| [MCP 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/index) | Prompt / Resource / Tool 的模型上下文与调用接口 | 只约束什么 Tool/Resource 可以被组织契约引用 |
| [A2A v1.0](https://a2a-protocol.org/v1.0.0/specification/) | Agent 间 Task / Message / Artifact、鉴权与任务内人工授权 | 不成为第二任务传输层；仍不替企业定义事实准入与业务真相 |
| [OASF](https://github.com/agntcy/oasf) | Agent 能力、domain、skill 和 module 元数据 | 能力描述不等于授权；OAC 补足 Source/Demand/权责/证据关系 |
| [ADC v0.1](https://github.com/Labs-R2-Advisory/adc-spec) / [AFS](https://github.com/agent-formation/afs-spec) | 单 Agent 委派授权；声明式 Formation 与组件可移植性 | OAC 不宣称首个 contract / Formation；差异是组织级 Source/Demand、parity、规范写入与 exact-run 证据闭环 |

这些层是可组合关系，不是多套“全能 Agent 框架”。OAC 的价值不在再造 transport，而在使**企业材料、
组织义务、人类权威、运行候选与最终业务状态之间的关系可移植、可拒绝、可验证**。

## 6. 复赛中怎样呈现，才不本末倒置

1. 主角是 Quote Operations Owner，主故事从“上游变化使既有成果过期”开始；“协作工作稿 → 上线日期确认稿 → 币种确认稿”是当前验证切片，不是产品定义。
2. OAC 企业适配是首次接入阶段：五类材料 → 候选映射 → 确定性校验 → 负责人等待并准入 → 不可变激活绑定。
3. 运行先展示既有成果与依赖基线，再由一次上游语义变化冻结 ChangeSet；Formation 只选择受影响领域的 Domain Agent。
4. 随后展示同一 Golden 运行中的 AgentTeams、Tool、Skill、Reviewer 和最终零写入 Preview；流程只在精确 Human Owner 权威点暂停，批准后选择性 Rebase。
5. 五条证据链和 ProductPath v0.3 只在验证档案中作为可复验证明出现；不把比赛评分或内部文件名放进产品界面。
6. 边界始终明确：OAC 不拥有 Runtime 或规范写入，生产就绪仍为 `false`。

## 7. 通用 OAC Plan 是下一阶段，不在当前 MVP 伪造

只有在 sibling `oac-spec` 完成以下内容后，才能宣称“OAC 自动生成并认证 Quote Agent 架构”：

```text
bounded enterprise-quote Profile
  -> deterministic compiler
  -> compiler-independent verifier
  -> at least two acceptable topologies + negative mutations + TCK
  -> OrganizationPlan -> PlanCertificate -> RuntimeBinding -> zero-effect bundle
```

该工作已作为独立待做规格固化，不混入当前收尾。

## 8. 永久主张防火墙

```text
AT task completed
  != Candidate admitted
  != Reviewer PASS
  != OAC Source/Demand admitted
  != OrgRebase Formation parity PASS
  != OAC Plan accepted
  != Human approved
  != Applied
  != Outcome accepted
  != Experience promoted
  != Real-enterprise validated
  != Production ready
```

这一组“不等号”是 OAC 与 OrgRebase 共享的核心，也是它们与“更强的单个 Agent”最根本的差异。
