# Pattern head 维护窗口

此命令只处理旧S05 `structured-domain-handoff` 的唯一合法Source迁移，以及现行Pattern head向**直接父版本**回退。它不会合格发布新Finance Skill，也不会打开自动采用。生产命令仅支持PostgreSQL；SQLite用于受控机制测试。

先由部署负责人盘点**所有**可能运行旧二进制的数据库登录角色，停掉旧应用/worker和新的采用入口。准备一个**独立**维护连接角色，具备当前tenant的正常StateStore读写权限及`pg_read_all_stats`，但不能是schema owner、superuser或`BYPASSRLS`。命令用当前`ORGREBASE_WORKSPACE_DB`连接；不要把旧writer角色继续用作维护角色。

由数据库管理员在窗口内将列出的旧writer角色设为`NOLOGIN`，结束其当前数据库连接，清除可继承/`SET ROLE`的成员关系，并确认当前库**除维护连接外没有任何client backend或prepared transaction**。旧会话可能在`REVOKE`成员关系后仍维持`SET ROLE old`；它在`pg_stat_activity.usename`下显示登录角色而非当前角色，因此仅查旧角色用户名不足以证明停写。维护命令先给出旧角色的具体失败原因，再检查全库其他客户端与prepared transaction，并在业务事务提交前重验；未满足即拒绝。管理员须在探针结束后关闭管理连接再启动命令。命令不会替管理员改角色、杀连接或发通知。窗口期间保持旧角色禁用并禁止新客户端接入；若有未列凭据在两次检查之间重新接入，或管理员撤销隔离，仍不能宣称完整持续隔离。共享角色原地切换不受支持，须准备独立维护角色。

使用owner控制且不可被组/其他用户写入的普通JSON文件，不要把JWT写进文件。`access_token_variable`只写环境变量名；该变量的值由短期当前治理身份令牌提供。配置必须带当前部署的四个分权actor、受审S05 bundle和installed predecessor摘要，以及exact待处理ref/digest；可从当前受权只读检查取得，不应手填猜测。迁移时`action=MIGRATE`，填写`expected_source_ref`、`expected_source_digest`、`expected_package_digest`；其余expected head字段和`reason`不填。回退时`action=ROLLBACK`，填写当前head的`expected_head_ref`、`expected_head_digest`、`expected_generation`、`expected_package_digest`和非空`reason`；source字段不填。两者均要求`enabled=true`、`maintenance_window_id`和完整`legacy_writer_roles`。

```bash
cd orgrebase
uv run python scripts/run_pattern_head_maintenance.py --config /secure/path/original-mutation.json --show-config-identity
uv run python scripts/run_pattern_head_maintenance.py --config /secure/path/original-mutation.json
```

`--show-config-identity`只读取owner控制的配置并打印其摘要，**不**验证数据库fence或执行变更。变更前保存这份`config_digest`和原配置文件；不要把JWT写入任一文件。成功回执只含head、generation、package、维护窗口摘要及低敏身份摘要。迁移创建`g00000001` MIGRATE head和完整资源快照，旧Source/候选/决定/回执原字节保持不变；回退追加新ROLLBACK generation且`adoption_enabled=false`。

若命令发出后响应丢失，**先不要换一个命令重试**。在维护隔离仍成立时另建`action=INSPECT`配置，填原变更文件的绝对路径`original_mutation_config_path`及事先保存的`expected_mutation_config_digest`，再填发出前已知的`expected_previous_ref/digest`（迁移时旧Source，回退时旧head）、预期新`expected_generation`、目标`expected_package_digest`和`expected_transition_kind`。保留与原命令相同的workspace、分权角色、受审包、旧角色清单和窗口ID。回读重载原配置、重新核验全库fence及角色OID，以原配置摘要重算窗口身份：MIGRATE必须与新head一致；ROLLBACK还必须由当前head引用的exact restoration receipt证明同一原命令的父head、目标版本/包、原因、治理Principal及窗口摘要。旧S05普通回退和另一个窗口的同形回退会HOLD。原配置遗失、被改写或事先身份摘要不可确认时，不能通过宽松形状判断报成功，应保持HOLD并人工审计事件链。新的迁移或回退必须基于重新取得的当前head和新的维护决定。

受控本地双后端/跨进程测试只证明这套代码与数据库门的机械行为；客户旧库、真实部署角色清单、停机和重启、业务上游资格还须在具体现场核验。历史录像与当前head不建立绑定。
