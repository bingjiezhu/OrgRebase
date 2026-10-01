# 许可与证据范围

OrgRebase 与 OAC 的项目自有代码、文档、Skill 和合成夹具适用 **[Apache-2.0](../../LICENSE)**。

| 内容 | 适用范围 |
|---|---|
| OrgRebase 自有代码、Skill、夹具与文档 | [Apache License 2.0](../../LICENSE) |
| 许可范围 | [LICENSE.md](../../LICENSE.md) |
| OAC 自有材料 | Apache-2.0；查看 OAC LICENSE.md |
| AgentTeams 及 Python 依赖 | 各自上游许可 |
| UCI 商品样本 | CC BY 4.0，保留来源和转换说明；不代表 UCI 认可产品 |
| 云模型与备用本地模型 | 各自服务或模型条款；软件客户端许可不替代模型许可 |

当前源码、文档站和分发包采用相同许可范围。旧版本条款与保留的元数据统一见[历史发行与封存记录](../../LICENSE.md#historical-releases-and-sealed-records)。

Vertex 和 DeepSeek 配置成功不自动授予客户数据外发权限。部署方应核对使用条款、数据流向与组织授权。备用 qwen2.5:3b 受 Qwen Research License 限制，商业使用需上游许可；OrgRebase 不分发其权重。

## 不混用验证等级

- 同运行回执证明所记录的任务、批准与结果，不证明客户付费。
- 历史公开交易可用于计价核验，受控税率和折扣不是真实历史政策。
- OWB 是平行参考 SUT 的构造机制对照，不是真实 LLM 单/多 Agent 优越性试验。
- 静态源码、安装测试与本地运行不能替代生产容量、客户 IAM 与连接器资格。

[完整第三方清单](../THIRD-PARTY-INVENTORY.md)和[历史/当前构建边界](../HISTORICAL-BUILD-VERIFICATION.md)保留更细说明。报告需保留实际运行身份、失败和未完成数量，并使用各材料对应的许可。
