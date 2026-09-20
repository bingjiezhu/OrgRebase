# 查询与缺失依赖见证

本文描述当前实现和可验证边界。查询见证用于约束提案依据，来源连接器负责证明观察范围；审批身份、影响分析和正式状态提交继续使用原有工作流。

## 支持的查询

`orgrebase.bounded-read-query.v1` 只接受一个已确认来源连接器、明确的记录 ID 集合、一个允许字段和一个操作。记录 ID 必须排序且唯一，字段必须在当前确认映射所允许的字段集合内。

| 操作 | TRUE | FALSE | UNKNOWN |
| --- | --- | --- | --- |
| `eq` | 至少一条已读记录的字段等于指定 JSON 标量 | 整个指定集合完整可见，所有字段均明确读到，且没有匹配 | 集合未完整观察、字段未读到、记录不可见、权限或时效无法确认 |
| `is_missing` | 至少一条仍存在且已读到的记录，其允许字段明确为 JSON `null` | 所有指定记录和字段均完整观察且全部非 `null` | 字段未返回、整个记录不存在、删除、404、权限缺失或观察过期 |

`is_missing` 不将空字符串解释为 `null`，也不将 HTTP 404 解释为已证明不存在。即使保存过删除 tombstone，当前实现仍不签发整个记录不存在的 TRUE 见证。通用查询契约上限为 1,000 个 ID；现有生产 Dataverse 来源确认及逐条回读上限更小，为 32 个记录，实际捕获受后者约束。

没有任意 SQL、任意过滤表达式或用户自报的覆盖证明。FALSE 的含义只限请求中列明的允许记录集合；它不表示整个 Dataverse 表、整个企业或者未纳入配置的未来记录都不存在匹配项。新增匹配记录、原来未观察的记录变为可见、或搜索集合扩展后，必须根据新的完整覆盖重新捕获见证。

## 持久契约

查询结果保存为普通不可变 artifact，ID 为 `read-witness:<内容摘要>`，媒体类型为 `application/vnd.orgrebase.read-dependency-witness+json`。`orgrebase.read-witness.v1` 同时绑定：

- 查询操作、值、明确记录集合和字段；
- 来源配置摘要、已确认映射摘要、metadata inventory 与确认世代摘要；
- 覆盖范围、完整性、连接器世代 ID；
- 持久 cursor revision 和摘要、最后一页 inbox 引用和摘要；
- 来源观察时间、到期时间，以及完整 coverage 摘要；
- TRUE/FALSE 结果和精确匹配记录 ID。

完整 coverage 摘要还覆盖已观察记录的 revision、字段值摘要、删除标识和当前逐条回读证据。原文值只用于查询请求的指定标量和来源既有入箱；见证不复制整页来源内容。

`orgrebase.read-dependencies.v1` 保存 1 至 32 个见证引用，绑定 artifact ID、见证摘要、查询摘要和期望结果。同一提案不能重复绑定同一个查询。该契约显式放入 `VersionedObject.payload.read_dependencies`，没有向所有历史业务对象添加默认字段。缺少此键的既有对象沿用原来的内容和摘要；存在此键却为 `null`、空列表、错误版本或错误引用时拒绝。

没有新增数据库表、迁移版本或第二套业务事实存储。

## 唯一产品路径

1. 已认证且拥有 `propose` 权限的调用者 POST `/api/workspace/read-witnesses`。请求体是查询；服务端从当前工作区的可信来源配置、确认映射和已提交 cursor/inbox 生成覆盖。调用者不能上传 coverage。
2. API 在当前工作区命令锁和同一 Store 事务内捕获见证。覆盖 UNKNOWN 时返回 `READ_DEPENDENCY_UNKNOWN`，不保存假阴性见证。
3. 返回 `witness` 和可直接使用的 `read_dependencies`。提案通过 POST `/api/workspace/change-proposals` 显式携带引用，作为提案 payload 的一部分参加摘要和审批绑定。
4. 普通 preview、责任人审批、明确 Apply 继续保留。见证不替代这些步骤，也不授予任何审批或外部写入权限。
5. `TaskContextCompiler` 对使用的实际前提验证见证；正式 Formation 提交会在事务内再次编译并验证。
6. `WorkspaceRebuildContextProvider.prepare` 在 RebaseWorkflow 的同一写事务里读取已提升的真实前提，验证见证后才形成 context manifest。多来源恢复也通过这个入口验证，没有额外恢复引擎。
7. 验证失败使 source promotion、Quote 后继、graph pointer、receipt、事件和幂等记录一起回滚。

只要待编译对象显式包含 read dependencies，却没有注入验证器，编译器直接报 `READ_DEPENDENCY_VALIDATOR_REQUIRED`，不会静默跳过。

## 失效与并发

完整 coverage 的任一版本绑定变化，即使查询布尔结果暂时相同，也使原见证失效，返回 `READ_DEPENDENCY_CHANGED`。这是保守版本约束，避免用新映射重新解释旧 inbox。权限、schema、分页、到期、未知记录和无法确认的删除等导致 coverage UNKNOWN，返回 `READ_DEPENDENCY_UNKNOWN`。

检查使用可信工作区时钟。重放编译时传入较早时间不能恢复已过期见证；当前时钟和请求时间取较晚者。

提交事务内的见证校验锁定当前工作区的 registry/append-head 行。来源 coverage、inventory、映射确认/撤销、不可用事件都在其业务事务内追加到同一个工作区事件头。因此，在本地数据库中，来源变更与见证提交有确定的先后顺序。校验不再领取来源 checkpoint 锁，避免逆转 source worker 的 checkpoint → append-head 锁顺序。

该锁只覆盖本工作区的短提交阶段。网络读取和模型生成不在此锁内；其他工作区使用不同事件头。见证不承诺来源外部系统与本地 PostgreSQL 的跨系统线性一致性：有效期内依赖的是最近完成的逐条回读，远端随后变化要由下一次同步观察到。实际有效期和回读资格应由部署要求决定。

见证也不是全表订阅、任意否定逻辑求解器或自动撤销所有既有外部效果的机制。业务仍应将变更纳入同一提案、审批和效果协议。

## 验证记录

对应历史档案 ID 为 `20260909-product-completion`，通过工作区外部归档索引查阅；该记录不是客户生产资格报告。

- `read-dependencies.log/xml`：30 项通过，包含 24 项契约/真实 Formation 流程测试、5 项真实受限 PostgreSQL + 签名 JWT + HTTP 产品路径测试，以及 1 项真实双连接提交顺序测试。
- `read-dependency-boundaries.log/xml`：13 项通过，包含 10 项架构边界、2 项已发布 PostgreSQL schema v2 的迁移/损坏回滚，以及上述 1 项真实两连接提交锁顺序测试。两份记录合计 42 项不重复检查。
- PostgreSQL 并发测试通过 `pg_blocking_pids` 观察来源失效写入实际被见证提交事务阻塞；前一事务内校验通过，失效事务提交后下一次校验 UNKNOWN。
- 正向端到端路径完成 capture → proposal → review → approve → Apply；负向路径在捕获 FALSE 后同步得到新匹配，再 Apply 返回 `READ_DEPENDENCY_CHANGED`，且 Quote/source/event head/outcome 均未错误推进。
- 第一页尚未结束、来源权限丢失和 TTL 过期均通过真实 API 拒绝；字段缺失/null、墓碑、映射/世代/范围/水位变化及错误摘要由独立负例验证。

端到端来源传输使用受控 Dataverse 响应，数据库、角色隔离、JWT 验证、HTTP API 和正式提交内核实际运行。它验证本地协议行为，不证明真实客户 tenant 的字段权限、数据质量、服务规模或业务收益。
