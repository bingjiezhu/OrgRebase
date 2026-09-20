---
name: enterprise-launch-readiness
description: Map an impact result to bounded launch and selective Rebase actions; 处理企业上线就绪评估、影响判断与选择性 Rebase 建议。
metadata:
  version: 1.4.2
  contract: contract.json
---

# 企业上线就绪 / Enterprise Launch Readiness

将新鲜 ImpactResult 映射为受限的上线、审阅或选择性 Rebase 建议。`SKILL.md`
是唯一 canonical 入口；输入必须符合 `orgrebase.launch-readiness-input.v1`
并绑定非零 Preview 与 approval receipt digests。

## 操作映射

- `AFFECTED_HARD` → `REBASE`
- `AFFECTED_REVIEW` → `REVIEW`
- `AFFECTED_INFORMATIONAL` → `NOTIFY`
- `UNAFFECTED_WITHIN_DECLARED_BOUNDARY` → `KEEP_CURRENT`
- `UNKNOWN` → `ESCALATE`，不得猜测 affected/unaffected
- 缺失或无效 classification → `ABSTAIN`

## 必须保持

- 不修改 canonical Claim、Work、Deliverable、Preview、evaluator 或 release state。
- 不扩张 Tool、domain、sensitivity、side effect、budget 或 canary scope。
- 结果始终是 `candidate_only=true`、`target_writes=0`。
- digest、Schema、权限或新鲜度失败时 fail closed，交回 control-plane review。

## 按需读取

- 中文请求、中文文件名或中英混输：读 [references/zh-CN.md](references/zh-CN.md)。
- 英文交付或英文协作环境：读 [references/en.md](references/en.md)。

## 证据上限

通过八分区评测只使包具备 Canary 资格，不代表企业审批后端已接受请求，也不授予本 Skill
自我发布权。
