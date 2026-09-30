# Quote 补证经验的受控 Skill 演化

当前实现只覆盖 `workspace-quote-evidence-recovery-v1`。它把 Quote 变化流程已有的
退回补证、恢复和最终结果摘要接入既有 `GovernedPatternService`，并为
`structured-domain-handoff` 生成一个内容型候选。它没有通用程序自改、跨企业学习或
自动生产晋级。

## 实际数据流

1. `quote_pattern_bridge` 从当前 Workspace 回读 ChangeEvent、补证 request、resume、
   Preview、Approval 和 outcome；case 只保留业务类型、计数和摘要，不复制报价值。
2. corpus resolver 在准入前重新读取这些记录。stable case 由
   tenant/workspace/event 组成，revision 由当前证据摘要组成；重试不能增加独立 case
   数量，证据变化不能继续使用旧证书。
   `APPLIED` 且有outcome才是`SUPPORT`；单独的业务`REJECTED`没有独立Skill-failure
   证书时保持`UNKNOWN`，不自动变成反例。
3. `SkillContentBundle` 只接受三个固定路径：instruction、reference 和一个 JSON
   checklist。资源以 exact bytes、大小和 SHA-256 写入候选；路径穿越、未知资源、
   新工具、副作用、错误前驱和内容替换都会拒绝。已安装 Skill、program bytes 和原
   registry head 不修改。通用direct service仍可保留其他内容作fixture评测；生产
   适配另外要求代码内审定的exact bundle payload/digest、target Skill和当前前驱全部
   一致。只保持同一路径但替换instruction/reference bytes不能进入生产候选。
4. 受限解释器实际消费 checklist。只有 handoff 明确声明本 profile 时，才要求
   request、resume、outcome 三个非零摘要；不完整时从 `HANDOFF` 变为 `ABSTAIN`。
   其他 profile 保留前驱行为，权限和目标写入上限仍为零。instruction/reference 当前
   只作为已装载资源保存，不冒充模型 prompt 消费。
5. 独立 evaluator 用冻结的八分区 replay 同时运行前驱与候选。业务 oracle 要求至少
   一条 HELD_OUT 的前驱失败被修复、所有 candidate gold 正确、零回归、checklist 有
   实际消费 trace。`NO_BEHAVIOR_DELTA` 会保存，但不能晋级。
6. 合格候选仍通过既有 Pattern Source、decision、evaluation 和 release history
   持久化。新进程从 canonical Source 恢复时重验 candidate、resource bytes、
   evaluation、decision、package digest 和 release history。生产路径还要重建corpus→proposal→
   candidate→evaluation→decision的Principal chain digest，并与Source中的digest相同。

整合测试会先从实际 Workspace recovery 表中构造一条 Quote case，通过resolver进入
同一corpus/candidate，再把它的exact consumer子集交给checklist。最终held-out gold
仍是独立冻结的受控合成评测，不冒充客户样本，也不把该gold写入候选资源。

## 身份与决策

`PrincipalPatternGovernance` 是生产适配层。它要求 current verified Principal，并把
corpus controller、candidate author、independent evaluator、release governor 配成四个
不同 actor。批准绑定 exact candidate digest、evaluation、当前 package head、workspace
scope 和幂等键；事务提交前再次执行当前授权。旧 fixture 路由没有开放，客户端传入的
actor 字符串不会获得权限。

corpus、proposal、candidate、evaluation和decision每个记录都保存当时验证的
issuer、subject、tenant、actor、expiry、workspace/scope digest和固定role phase。Proposal与
candidate必须是同一author身份；corpus controller、author、evaluator和governor的
issuer/subject必须构成四个不同身份。这些历史字段用于重启后复核，不把过期token
重新当成当前授权。
历史Principal token的自然过期不回溯撤销已完成的审批事实；标准新run采用期限由独立
policy的`adoption_not_after_epoch`表达，并在dispatch和result commit前重读。

可执行 steward 命令是：

```sh
python -m orgrebase.workspace.pattern_governance --config /private/pattern-decision.json
```

它只处理一个已存在候选的 `ADMIT` 或 `REJECT`，要求生产部署、PostgreSQL、JWT 成员
配置和私有常规配置文件。配置必须显式 `enabled: true`，包括 workspace、四个 actor、
candidate/evaluation refs、两个 expected digests、reviewed bundle/target/predecessor、verdict、
token 环境变量名和幂等键。配置内的reviewed值仍必须与代码内审定bytes重算结果相同。
命令不会创建候选，也不会打开自动采用。仓库没有附带可直接执行的生产身份或 token。

当前受控本地测试使用模拟但结构正确的 Principal 验证机制。`decision` 保留
`human_review_verified=false`，并把实际员工操作单独标为未运行；测试中的签名主体不能
冒充客户 Skill Steward 已阅读并批准。

## 下一次运行、关闭和恢复

正式产品入口是调用方显式发起的补证交接检查：
`POST /api/workspace/governed-learning/quote-recovery-runs`，实现在
`quote_recovery_operations.py`。它接受canonical recovery event、已批准exact candidate
和全新run id；结果尚未形成时返回`ABSTAIN`，实际Apply并形成outcome后，另一个全新run才可
返回`HANDOFF`。该入口不会由普通Quote自动触发，也不会自动生成下一代候选。
模块从私有常规文件读取显式policy；未配置、`enabled=false`、
workspace/tenant/candidate/actor不匹配或`adoption_not_after_epoch`过期均fail closed。Policy只提供
产品hook，不代替Principal chain、独立evaluation或exact decision复核。

入口会从当前event重建case/resolver、生成exact consumer input并调用
`PrincipalPatternGovernance.invoke`。首次请求先持久冻结operation key、完整Principal identity、
policy、command、case/certificate和consumer input digest；同operation key原样重试从该冻结意图及
canonical reservation/result/terminal恢复，即使业务后来Apply也仍返回原`ABSTAIN`，不把旧操作
解释成新输入。同candidate+run只能绑定一个operation key；换key或actor不能重派或重绑UNKNOWN。
响应只包含case/input/package/output/receipt digest、稳定状态与范围字段，
不复制Quote私密值。

历史查询为
`GET /api/workspace/governed-learning/quote-recovery-operations/{operation_key}`。它要求当前读权限，
但不要求采用policy仍开启或未到期；查询只核对冻结意图和既有终态，不签发采用authority、
不调用consumer。因而关闭新采用后仍能判断旧操作是否完成。

`PatternAdoptionPolicy` 默认 `enabled=false`。只有部署显式列出 exact candidate ref，
新 run 才能调用；每次调用保存自己的 run/task/input/output、release、bundle、已装载
资源和实际消费 checklist 摘要。它是纯确定性解释器，不进行模型或网络 I/O，模型成本为
零；instruction 模型路径仍未运行。旧 Quote、审批和历史调用不回写。
仅开启candidate ref不足以采用：Principal adapter还会验证五阶段身份链、审定
bundle、evaluation、decision authority basis、Source与production-chain digest。由旧direct
`GovernedPatternService`/fixture controller产生的记录仍可历史读取，但生产decision和invoke
会以`PATTERN_PRODUCTION_CHAIN_REQUIRED`拒绝。
当前产品scope只允许一个代码内审定的Quote-recovery overlay。从该overlay继续生成N+1
后继head、多个reviewed bundle版本和不撤回的successor CAS仍是后续范围；本规格不宣称通用
持续自我演化。
核心`GovernedPatternService.invoke` 本身也必须收到当前授权检查和由生产适配在完整链
校验后签发的一次性运行内authority；两者任一缺失都拒绝。保留的受控fixture使用
`tests/`下的helper显式调用private低层；生产class不公开fixture执行方法，产品模块也不得
访问该private路径。

candidate invoke使用三段边界：短事务捕获Source/evidence/head/dependency/Principal并保存
reservation；在锁外执行确定性consumer；结果提交前重读上述全部绑定并再授权。
如果consumer运行期间Source/证据被撤回、head/依赖漂移或Principal撤权，迟到结果
只保存`invocation-rejection`，不保存`invocation-result`，也不会成为可采用的成功回执。
短事务内会再解析dependency/head并重算capture digest；从初始捕获到reservation之间的漂移同样
在consumer前拒绝。Consumer抛出可判定异常时保存`FAILED`，结果不可判定时保存
`RESULT_UNKNOWN`；已有reservation/result/rejection/terminal的同candidate+run scope不得重派。
持久的reason code由固定异常类/白名单映射产生；未知异常统一为
`PATTERN_INVOCATION_RESULT_UNKNOWN`。系统不会把exception message写入artifact。这是该调用边界的
窄安全合同，不声称通用DLP或所有日志系统已被覆盖。
恢复前驱consumer也使用同一类映射；异常会持久
`predecessor-invocation-terminal` 的`FAILED`/`RESULT_UNKNOWN`，而不只留一条无结论reservation。

证据撤回或 canary 退化会沿既有依赖图把 Source 标为
`REQUALIFICATION_REQUIRED`，新采用随即失败。受权恢复只在 exact installed predecessor
bytes 仍匹配时保存 restoration receipt，并保持采用关闭；该记录证明受控本地前驱 bytes
可用，不等于外部部署已回滚。Quote 或 Dataverse 已发生效果仍走原 Change/Apply/效果
对账流程。
恢复后的前驱不会暗中重放旧run：调用方必须显式使用restoration ref和全新run id。
成功时产生`EXACT_PREDECESSOR_RESTORATION`回执；已出现过reservation/result的run id会拒绝。
该唯一性在workspace全局Pattern事务锁中按run id校验，不包含actor；两个actor并发恢复
同一run也只能有一个reservation和一个result，且PostgreSQL重启后仍不可重放。

## 当前证据边界

已通过 SQLite 的资源篡改、路径穿越、独立 oracle、无行为变化拒绝、Principal 角色
分离、head CAS、幂等重试、提交前再授权、跨 OS 进程恢复、新 run 消费、默认关闭、撤回
和前驱 bytes 恢复测试，以及未审定bytes与legacy direct chain的生产拒绝；
PostgreSQL 受限运行角色使用完整Principal chain重开调用和双连接并发决定也已通过。
另外已覆盖reviewed content依赖/前驱/head漂移、决定后证据撤回、在途Source撤回、
Principal撤权、canary退化、全新run前驱恢复，并校验旧Quote、旧调用和外部effect记录不变。
真实员工操作、真实客户 UAT、真实 Vertex 调用、生产 canary 流量和业务收益仍为
`NOT_RUN`。这个 profile 的确定性
checklist 不需要模型；未来 instruction 分支如使用模型，只允许经已资格化的 Vertex
`gemini-3.8-flash` 合同另行验收。
