# Skill 包与调用边界

系统架构见 [SYSTEM-MAP](SYSTEM-MAP.md)。这里说明三个自有 Skill 的制品、执行入口、
资格检查与复用范围；包可调用不等于企业业务已经验收。

机器来源：

- `configs/workspace/skill-registry.json`
- `skills/enterprise-quote-compose/`
- `skills/structured-domain-handoff/`
- `skills/enterprise-launch-readiness/`
- `src/orgrebase/workspace/skill_packages.py`
- `src/orgrebase/workspace/experience.py`
- `evidence/golden-competition/latest/pilot/`

## 统一生命周期与证据边界

三个 Skill 通过精确字节发现、装载、八分区资格检查、运行内发布权限和受限调用。
`skill_foundry.RestrictedSkillInterpreter` 只接受 `REQUIRE_FIELDS / MAP_VALUE / RETURN_FIELD`，
不执行任意 Python 或扩大工具权限。实际包调用还经过 `skill_packages` 中的专用 adapter，
检查 Schema、任务/运行绑定、非零依赖摘要、四域结果根和拒绝标志；不能把完整调用路径
概括为一次查表，也不能把这些校验写成自主学习或通用领域推理。

当前 Golden 的 Quote 资格用例由 `competition_run._quote_skill_evaluation_cases` 构造，
八分区各一条。`HELD_OUT` 在这组用例中是预声明标签变化，不是独立抽样的留出数据；
`CANARY` 是受控调用分区，不是生产流量分配。门槛 1.0 表示每条已声明断言必须满足，
不是统计置信度或泛化率。`baseline_output_digest` 中的 `NOT_SCORED` 不构成成对收益对照。
资格检查可因完整性、权限或预期动作不符而失败，但不需要故意破坏成功制品制造失败证据；
失败边界由 `tests/workspace/test_skill_package_lifecycle.py` 的负例独立验证。
manifest v2 把 `SKILL.md`、contract、program 以及 input/output Draft 2020-12
JSON Schema 的 exact bytes 一起放入 wheel；Loader 只解析包内本地 `$ref`，
外部或无法解析的 Schema ref 会 fail closed。Retained lifecycle 从隔离的
`INSTALLED_WHEEL` 路径完成 discover / load / evaluate / requalify / invoke，
不把 `SOURCE_CHECKOUT` 调用当作分发验收。
每个包只保留一个 canonical `SKILL.md`：`description` 同时包含中英文发现词，
正文中文优先，再按需装载 `references/zh-CN.md` 或 `references/en.md`。中文口语、
英文、中英混输和中文文件名均进入评测用例；不新建与执行入口可能漂移的
`SKILL.zh.md`。
`REPLAY / HELD_OUT / NEGATIVE_TRANSFER / PERMISSION / INJECTION / MALFORMED /
RESOURCE_OR_DEADLINE / CANARY` 八个分区都要达到各自声明的门槛，安全分区必须
100% 通过，且 `target_writes=0`。发布权威目前是
`PROCESS_LOCAL_EXACT_OBJECT_MEMBERSHIP`：它是受控本地信任模型，不是持久化签名服务。
依赖漂移后必须用新评测收据和已恢复的 exact dependency set 经过
`REQUALIFICATION_REQUIRED → EVALUATED → SHADOW → CANARY`；旧收据不能重放授权。
当前 Golden 主链只使用其自身的发布/调用收据；retained Spec 052 已另行验证
`enterprise-quote-compose@1.3.1` 精确恢复并受限调用 direct predecessor `1.3.0`，
并通过幂等重放、进程状态重建与权限/系谱/绑定篡改拒绝。该独立事件不能
拼入当前 Golden run，也不等于生产 Registry 回滚。

## 1. structured-domain-handoff 1.1.2

| 字段 | 内容 |
|---|---|
| Skill 名称 | `structured-domain-handoff` |
| Skill 类型 | 自定义 Skill / AgentTeams 传输适配 |
| 使用场景 | Workspace 形成联盟：Product / Legal / Finance / GTM 按已编译 `DomainDelegationTask` 返回本域候选 |
| 输入参数 | package-specific 最小投影：`run_id` / `task_id` / `delegation_id`、delegation-task 与 context-projection 摘要、结构化 candidate bundle |
| 输出结果 | `orgrebase.domain-transport-candidate.v1`；candidate-only Domain bundle；`target_writes = 0` |
| 调用条件 | 仅当任务由已准入 `CoalitionPlan` 编译；禁止自由扩权、扩上下文或扩输出 Schema |
| 依赖工具 / 系统 | 当前受限包不调用外部工具；`STRUCTURED_DOMAIN_HANDOFF_V1` 由产品侧 adapter 解释包内字段闭包、权限/拒绝规则与最小上下文；制品不支持脱离该 runner 任意执行 |
| 失败处理 | 缺权威、过期、用途/接收者不匹配、Schema 失败、超时或要求扩权时 ABSTAIN；零写入 |
| 权限与安全 | 不能准入 Claim/Policy；不能改 Work 状态；不能写图或 Apply；Legal 原文不得离开 Legal Worker |
| 复用价值 | 已在报价路径的 Product / Legal / Finance / GTM 四个 Worker 间复用；其他团队或场景仍须映射其输入契约、权限和候选 Schema 后单独验收 |
| 与多 Agent 关系 | 四个 Domain Worker 共用此 Skill；控制面拥有准入与状态 |

## 2. enterprise-quote-compose 1.3.1 release artifact

| 字段 | 内容 |
|---|---|
| Skill 名称 | `enterprise-quote-compose` |
| Skill 类型 | 自定义 Skill / 原候选之上的精确发布制品 |
| 使用场景 | 在精确 Tool 返回与已准入报价字段之上生成 candidate-only Quote 结果 |
| 输入参数 | 具体安全请求字段、精确 program digest、非零 Tool receipt/result digests、`CoalitionResultBinding` digest 与 Product / Legal / Finance / GTM 四个 exact result roots |
| 输出结果 | 受限字段映射候选；回传 Tool、coalition 和四域摘要；`candidate_only=true`；`target_writes=0` |
| 调用条件 | 只有持有当前 evaluator 的 release ledger head 可调用；公共 Registry 不接受调用方自报的 release receipt |
| 依赖工具 / 系统 | 不直接调用 Tool；必须消费同一运行已完成 HTTP Tool 调用的两个摘要 |
| 失败处理 | digest 不一致、分区失败、净增益不足、回归或安全失败则 QUARANTINED |
| 权限与安全 | 仅允许 REQUIRE_FIELDS / MAP_VALUE / RETURN_FIELD；具体字段驱动 permission/injection/resource 拒绝；不能写规范状态或自行发布 |
| 复用价值 | 已验证报价候选的精确依赖绑定与受限组装；发布说明、合规摘要尚无同等级包调用证据，不能直接套用报价模板。可复用的是加载/资格/发布机制，业务输入和 handler 需要另行适配 |
| 与多 Agent 关系 | 组合前必须绑定同一运行的四域 accepted roots；任一域缺失、替换、零摘要或重复都拒绝；不声称 learned Skill |

当前成熟度：受控本地可发现、可装载、可调用且有评测/发布收据。历史
`SkillCandidateArtifact` 仍保持 `executable=false`；1.3.1 是当前另行授权的 release
artifact，1.3.0 是其已验证可恢复和受限执行的 exact direct predecessor。
当前构造分区只证明 exact package、受限解释器和运行内治理接线，不证明
Skill induction、跨企业泛化、持久化发布信任或生产净增益。

## 3. enterprise-launch-readiness 1.4.2

| 字段 | 内容 |
|---|---|
| Skill 名称 | `enterprise-launch-readiness` |
| Skill 类型 | 自定义 Skill / 影响-行动映射 |
| 使用场景 | Core 变化咨询：把确定性 Impact 结果映射为有边界的 rebase 行动建议 |
| 输入参数 | package-specific 输入：`classification`、`object_id`、`reason_code`、非零 Preview 与 Approval receipt digests |
| 输出结果 | `orgrebase.skill-action.v1`：REBASE / REVIEW / NOTIFY / KEEP_CURRENT / ESCALATE / DENY / ABSTAIN |
| 调用条件 | 非零 Preview/Approval 摘要与 package 程序均通过精确加载；缺失任一摘要则 abstain |
| 依赖工具 / 系统 | 无副作用工具；适配器 `agentteams/skills/enterprise-launch-readiness/SKILL.md` |
| 失败处理 | FAIL_CLOSED；返回 ABSTAIN 或 ESCALATE 及机器可读原因 |
| 权限与安全 | 不能准入事实或写规范状态；敏感度 INTERNAL；side_effects 为空 |
| 复用价值 | 已在受控 Core 变化咨询中执行；其他系统可适配相同分类和行动契约，但必须提供有效 Preview/Approval 绑定。仅传入另一个对象名不证明跨业务闭环已复用 |
| 与多 Agent 关系 | skill-curator 产出候选；change-coordinator 消费建议；控制面拥有发布状态 |

## 运行后经验治理（不是第四个 Skill）

Specs 059–060 的正式 run
`run:golden-competition:8e5f49fe-ba22-4107-93d3-48194333b308` 核对 Finance
`ABSTAIN → REPLAN → HTTP Tool → PASS` 的同运行状态、缺槽原因和精确收据，
匹配预声明恢复模式后登记候选；不符合模式则 `NO_CANDIDATE`。候选绑定来源轨迹，
但其恢复步骤和执行映射由代码预声明，不由模型从轨迹归纳生成。目标是改进现有
`structured-domain-handoff`，而不是新增第四个 Skill 或第二套 Registry。

| 字段 | 当前冻结事实 |
|---|---|
| 候选结论 | `IMPROVE existing structured-domain-handoff` |
| 成熟度 | `SINGLE_RUN_SEED`；只代表一个受控本地 run |
| 权限 | candidate-only；read-only Tool；`target_writes=0`；不能删除人工门或扩大接收者/用途 |
| 评测 | `REPLAY / HELD_OUT / NEGATIVE_TRANSFER / PERMISSION / INJECTION / MALFORMED / RESOURCE_OR_DEADLINE / CANARY`，8/8 |
| 人工治理 | `human:skill-steward` 绑定 exact candidate/evaluation/head digest，服务端等待 4000ms 后显式批准 |
| 发布结果 | `APPROVED_CANARY`；批准后 exact package 可 discover/load/invoke；`RELEASE` dry-call `SUCCESS` |
| 与本次 Quote 的关系 | `current_quote_consumed_candidate=false`；本次 Quote 不倒灌、不重算 |
| 权威边界 | 经验候选、评测、批准与 dry-call 均 canonical writes=`0`；业务真相仍由 StateStore/RebaseWorkflow 管理 |
| 未证明 | 多运行重复性、跨企业泛化、生产 canary 流量、自动自治改写，均 `NOT_RUN` |

`APPROVED_CANARY` 只说明 exact 候选经过当前受控发布账本获得后续 dry-call 权限；它不把一次经验
升级为通用知识，也不让 Skill 绕过下一次运行的任务、权限、准入、Reviewer 或人工审批。

许可：项目自有 Skill 适用 Apache-2.0。封存清单里的 `PolyForm-Noncommercial-1.0.0` 是内容寻址标识，不缩小当前授权。
