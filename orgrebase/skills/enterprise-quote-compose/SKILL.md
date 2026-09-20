---
name: enterprise-quote-compose
description: Compose an enterprise quote candidate from bound Product, Legal, Finance, and GTM results; 处理企业报价编排、跨域结果汇总与报价候选生成。
metadata:
  version: 1.3.1
  contract: contract.json
---

# 企业报价编排 / Enterprise Quote Compose

仅在 Product、Legal、Finance 和 GTM 四个领域结果已绑定到同一
coalition 时生成报价候选。`SKILL.md` 是唯一 canonical 入口；
`package.json`、`contract.json` 和本地 JSON Schema 约束精确执行。

## 必须保持

- 校验精确 `program_content_digest`、Tool 收据、coalition root 和四域 digest。
- 仅执行 `REQUIRE_FIELDS`、`MAP_VALUE` 和 `RETURN_FIELD`。
- 结果始终是 `candidate_only=true`、`target_writes=0`。
- 权限扩张必须 `DENY`；注入、畸形输入、资源或 deadline 故障必须 fail closed。
- 缺失、替换、零值或重复域根时不得编排。
- 发布、激活和 rollback 只归 Skill Registry Authority；历史 candidate 仍为
  `executable=false`。

## 按需读取

- 中文请求、中文文件名或中英混输：读 [references/zh-CN.md](references/zh-CN.md)。
- 英文交付或英文协作环境：读 [references/en.md](references/en.md)。

## 证据上限

本包只证明受控本地的精确打包、装载、评测、发布和受限调用；不证明已从员工行为中
学会 Skill，也不证明对新企业的泛化能力。
