# 企业上线就绪说明

## 适用请求

当用户要求“判断上线变更是否需要 Rebase”、“根据 ImpactResult 给出最小处置”、
“检查企业上线就绪性”，或引用 `上线审批单-最终版.pdf` 等中文文件名时，使用本 Skill。
输入必须是控制面已编译的结构化影响结果，不接受 free-form 文本直接改变权威范围。

## 决策映射

- 硬依赖受影响：`REBASE`。
- 需人工判断：`REVIEW`。
- 仅信息性影响：`NOTIFY`。
- 在声明边界内不受影响：`KEEP_CURRENT`。
- 无法证明：`ESCALATE`。
- 无效输入：`ABSTAIN`。

## 输入、输出与权威

必须绑定 `object_id`、`reason_code`、Preview receipt 和 approval receipt。输出只是处置
candidate，保持 `candidate_only=true`、`target_writes=0`。它不能修改 canonical state，不能自己
发布，也不能将评测通过视为企业审批。

## 失败语义

任何 digest、Schema、权限、新鲜度或 scope 异常都必须 fail closed，并请求 control-plane review。
