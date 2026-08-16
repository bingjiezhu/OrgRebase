# Skill 清单（附录 B）

对应 GOAI Agent Infra 参赛手册第 9.1 节与附录 B。Skill 是必选项。本项目使用自定义 Skill，不声明使用阿里云官方用云 Skills。官方云 Skill 可在同一契约后接入，不改控制面。

机器来源：

- `skills/enterprise-launch-readiness/contract.json`
- `agentteams/skills/enterprise-launch-readiness/SKILL.md`
- `agentteams/workspace/skills/structured-domain-handoff/SKILL.md`
- `src/orgrebase/workspace/skill_foundry.py`（`enterprise-quote-compose@1.1` 候选）

## 1. structured-domain-handoff 1.0.0

| 字段 | 内容 |
|---|---|
| Skill 名称 | `structured-domain-handoff` |
| Skill 类型 | 自定义 Skill / AgentTeams 传输适配 |
| 使用场景 | Workspace 形成联盟：Product / Legal / Finance / GTM 按已编译 `DomainDelegationTask` 返回本域候选 |
| 输入参数 | 精确 `DomainDelegationTask`：组织、员工任务、TaskTemplate、槽位、actor 投影、Worker 身份、run / nonce / deadline、允许输出 Schema |
| 输出结果 | `orgrebase.domain-transport-candidate.v1`；candidate-only Domain bundle；`target_writes = 0` |
| 调用条件 | 仅当任务由已准入 `CoalitionPlan` 编译；禁止自由扩权、扩上下文或扩输出 Schema |
| 依赖工具 / 系统 | 声明的 Domain 读取口；可选模型提供方；不调用规范状态写工具 |
| 失败处理 | 缺权威、过期、用途/接收者不匹配、Schema 失败、超时或要求扩权时 ABSTAIN；零写入 |
| 权限与安全 | 不能准入 Claim/Policy；不能改 Work 状态；不能写图或 Apply；Legal 原文不得离开 Legal Worker |
| 复用价值 | 任意需要“按权威域交候选、控制面再准入”的跨域任务可复用同一交接契约 |
| 与多 Agent 关系 | 四个 Domain Worker 共用此 Skill；控制面拥有准入与状态 |

## 2. enterprise-quote-compose 1.1 candidate

| 字段 | 内容 |
|---|---|
| Skill 名称 | `enterprise-quote-compose` |
| Skill 类型 | 自定义 Skill / 声明式受限程序候选 |
| 使用场景 | 把报价形成经验封装为可评测候选；当前 Workspace Skill Foundry 主线 |
| 输入参数 | 已准入报价槽位值；exact candidate bytes / digest；冻结评测分区 |
| 输出结果 | 受限字段映射结果；`SkillEvaluationReceipt`；发布状态仅能为 CANARY 或 QUARANTINED |
| 调用条件 | Curator 提交不可变候选后，由独立 Evaluator 加载 exact bytes；不能用仓库实现替换候选 |
| 依赖工具 / 系统 | 无工具调用；`executable = false`；无副作用 |
| 失败处理 | digest 不一致、分区失败、净增益不足、回归或安全失败则 QUARANTINED |
| 权限与安全 | 仅允许 REQUIRE_FIELDS / MAP_VALUE / RETURN_FIELD；不能写规范状态或自行发布 |
| 复用价值 | 报价、发布说明、合规摘要等“按已准入槽位组装交付物”的任务可复用同一评测门禁 |
| 与多 Agent 关系 | 形成闭环的经验沉淀步骤；GTM 渲染引用该 Skill 版本，变化后触发重新资格认证 |

## 3. enterprise-launch-readiness 1.3

| 字段 | 内容 |
|---|---|
| Skill 名称 | `enterprise-launch-readiness` |
| Skill 类型 | 自定义 Skill / 影响-行动映射 |
| 使用场景 | Core 变化咨询：把确定性 Impact 结果映射为有边界的 rebase 行动建议 |
| 输入参数 | `orgrebase.impact-result.v1`：`classification`、`object_id`、`reason_code`、`preview_digest` |
| 输出结果 | `orgrebase.skill-action.v1`：REBASE / REVIEW / NOTIFY / KEEP_CURRENT / ESCALATE / DENY / ABSTAIN |
| 调用条件 | 已准入 ChangeSet 且 Preview 新鲜；调用方出示精确 preview digest；对象在评价范围内 |
| 依赖工具 / 系统 | 无副作用工具；适配器 `agentteams/skills/enterprise-launch-readiness/SKILL.md` |
| 失败处理 | FAIL_CLOSED；返回 ABSTAIN 或 ESCALATE 及机器可读原因 |
| 权限与安全 | 不能准入事实或写规范状态；敏感度 INTERNAL；side_effects 为空 |
| 复用价值 | 任何发出公开 ImpactResult Schema 的系统可接入同一行动枚举 |
| 与多 Agent 关系 | skill-curator 产出候选；change-coordinator 消费建议；控制面拥有发布状态 |

许可：项目自有 Skill 合同遵循仓库 `LICENSE`（PolyForm Noncommercial 1.0.0）；商用须书面授权。
