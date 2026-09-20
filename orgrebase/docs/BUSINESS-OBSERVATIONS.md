# 业务效果与成本观测

企业价值验收采用同一业务案例的现有流程与产品流程配对观测。`business-report` 计算已记录的结果和成本，不负责确认记录真实，也不把测试通过转成收益承诺。

## 数据要求

输入是 JSON 数组，每项包含 `case_id`、`organization_id`、`cluster_id`、`period`、`currency`、`evidence_class`、`independent_annotation_ref`、`baseline`、`product`。相关变更放在同一 `cluster_id`，不能把同一变更引出的几十次工具调用当作几十个独立样本。跨组织、时间留出及盲标方案在采集前确定。

`evidence_class` 必须选择 `SYNTHETIC`、`CONTROLLED_LOCAL` 或 `REAL_ENTERPRISE`。最后一种仍需独立核对原始证据，不能仅靠标签获得资格。

每条流程记录包含：

| 字段 | 要求 |
|---|---|
| `run_ref` | 可追溯的运行记录 |
| `outcome` | `QUALIFIED`、`REJECTED`、`UNKNOWN`、`FAILED`、`INCOMPLETE` 或 `HUMAN_HANDOFF` |
| `result_evidence_ref` | 合格结果必须有证据引用 |
| `unexpected_mutations` | 未批准或超范围变化次数，有此类变化不能标合格 |
| `costs` | 每笔费用的独立观测引用、类别、十进制字符串金额 |

费用必须覆盖 `model`、`tools`、`human_review`、`rework`、`operations`、`onboarding`；无支出也显式记录 `"0"`。原流程执行工时与负责人复核工时均计入 `human_review`，用各自的观测引用区分。共享实施成本先制定摊销方法并生成不同分摊记录，不得重复计入同一个费用引用。币种不得直接混加，需要按预先确定的汇率和来源转成同一币种。

金额使用字符串，例如 `"0.10"`，不接收浮点数、NaN、Infinity 或负值。原始工时、单价、发票和人工判断保留在客户控制的证据系统，报告仅引用，不内嵌敏感业务正文。

## 执行与解释

```sh
orgrebase business-report /private/observations/paired-cases.json --output /private/reports/paired-report.json
```

报告保留失败、未知、转人工和未完成的分母与成本。`cost_per_qualified_result` 使用全部成本除以合格结果数；没有合格结果时为 `null`。净节省为负、为零，或者合格结果减少、越权变化增加，返回 `REDUCE_SCOPE_AND_INVESTIGATE`，应缩小自动化范围并检查原因。

输入摘要绑定该次报告的完整记录。报告提供组织数、时期数、相关簇数，但不假装计算了统计显著性。`enterprise_roi_proven` 始终为 false；真实来源核对、足够样本、独立标注、风险接受及客户继续使用意愿属于独立业务验收。不能把同一个报告中的“成本下降”独立摘出，隐藏完成率或错误增加。

`tests/test_business_evaluation.py` 验证失败分母、完整费用、十进制精度、重复计费拒绝及负收益处理；这些测试只证明计算契约。
