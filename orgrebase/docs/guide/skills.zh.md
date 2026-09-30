# Skill与企业包复用

## 现有三个Skill

| Skill | 用途 | 边界 |
|---|---|---|
| structured-domain-handoff | 四领域共享任务与候选交接契约 | 缺绑定、越权或Schema错误时拒绝或弃答 |
| enterprise-quote-compose | 绑定同运行Tool与四域结果，组装报价候选 | 不自批、不写规范状态 |
| enterprise-launch-readiness | 将已验证影响分类映射为有限行动建议 | 不把Unknown改为肯定结论 |

包包括SKILL.md、contract、program、Schema和版本manifest。注册表绑定精确版本与包摘要；解释器和对应adapter按包内契约校验输入、执行受支持程序并记录输出回执。上传任意Python文件不会使其成为可执行Skill。

“兼容与恢复验证”在隔离账本中执行以下检查，并绑定完整三项Skill目录摘要：

| Skill | 版本与验证范围 |
|---|---|
| enterprise-quote-compose | `1.3.0 → 1.3.1 → 1.3.0`：资格评测、试调用与旧版本实际恢复调用，共12次调用 |
| enterprise-launch-readiness | `1.4.1 / 1.4.2`：六个受控案例分别调用新旧版本，再回放一次旧版，共13次调用 |
| structured-domain-handoff | `1.1.1 / 1.1.2`：同样执行六组新旧对照与一次旧版回放，共13次调用 |

完整成功运行共38次调用。后两项范围为`ISOLATED_COMPATIBILITY_AND_PREDECESSOR_INVOCATION`：验证保留版本字节的兼容性及实际可调用性，不代表切换了生产Skill。每组新旧版本的执行程序相同，不能据此声称算法升级。验证不会更改工作区报价或正式能力注册表；以[Skill注册表](../../configs/workspace/skill-registry.json)和本次验证回执为准。

当前资格套件为 `orgrebase.quote-skill-qualification.v2`：八个分区共九个案例，包括把法务结果摘要替换为产品摘要、仍请求正常组装的负例；该输入必须弃答。验证缓存绑定套件版本和摘要，旧八例通过记录不会替代本轮资格，历史封存记录仍按原套件核验。

八分区检查是构造性资格，不是独立样本泛化率。HELD_OUT与CANARY在当前案例集中的标签不等于统计留出或生产流量灰度。跨企业或新成果类型需要另外的handler、领域测试与业务验收。

## 配置企业事实，不改引擎

按照[企业包练习](../REUSE-AND-LICENSING.md#try-an-enterprise-pack-without-changing-the-engine)初始化草稿、修改受支持的产品方案与币种事实、重新 seal 到新目录并运行检查。这不是把自由文本政策自动转成可执行规则；不要手改摘要，也不要把不完整来源标成 `COMPLETE`。

保留治理流程、权威检查和回执；替换企业事实、负责人、来源映射与目标适配器。默认 `single-quote` 工作区管理一个 Quote；显式启用的 [Quote＋Discount Memo 双成果配置](../QUOTE-DISCOUNT-MEMO.md)在新建隔离工作区中管理恰好两种规范成果，当前仅有受控本地验证且不写外部系统。四个领域 Worker 共享 handoff 是已实现复用；任意新成果仍需独立 handler、来源绑定和验收。

完整包接口与资格说明见[Skill清单](../SKILL-LIST.md)。
