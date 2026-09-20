# Plan — Spec 009

## Implementation order

1. Add the three portable models in one focused module and register them without changing existing resource
   bytes.
2. Implement pure lifecycle-root projection/verification functions with stable reason codes.
3. Export schemas and registry entries; package them in the wheel.
4. Add positive and adversarial TCK vectors, including `ACCEPT/COMPLETED/REJECT`.
5. Validate through the public CLI and a fresh built-wheel process.

## Boundary

`ExecutionReceipt`, `OutcomeObservation`, `ProcedureContract`, `EvolutionProposal` and governance commands may
be runtime extensions. OAC Core sees them only as exact typed `ResourceRef`s unless later independent
implementations demonstrate a stable common wire contract. This keeps the standard's narrow waist smaller than
the product implementation.

## Verification ladder

```text
model unit tests
-> schema/registry drift
-> root mutation tests
-> public CLI validation
-> TCK fixture replay
-> built-wheel clean-process replay
-> legacy digest regression
```

## Rollback

New kinds and fixtures are additive. If root semantics fail review, do not publish the profile; retain existing
Core and Runtime Lowering artifacts unchanged. No existing kind is deleted or rewritten.

## Historical seed-2 implementation evidence

- focused evolution/Core/schema/runtime-lowering regression suite: 78 passed;
- deterministic schema export followed by `--check`: passed;
- isolated built-wheel import and public evolution-registry replay: passed;
- historical evidence repair: old Supplier, Plan-verification and benchmark coordinates retain their exact
  byte identities; integrity and successor-behavior checks no longer impersonate one another;
- historical Spec 009 evidence: `oac.evolution.minimum/v0.1-seed-2` binds its original source, schemas, contracts, one
  recomputed root vector, nine named controls and a single-reference-implementation claim ceiling;
- evidence policy: `docs/validation/HISTORICAL-EVIDENCE-VERSIONING.md` forbids in-place re-signing and
  defines the successor publication stop conditions.

The source-type and CTK schema-export update uses a new seed-2 evidence coordinate. Seed-1 bytes remain immutable; seed-2 captures and verifies each predecessor source/schema/contract/vector byte separately. The three core Kinds, root algorithms, published root vector and claim ceiling are unchanged.

本轮 CTK 最小化另发现并修复已准入 raw-map 在计划验证时被默认字段重新物化的身份错位。新 `verify_plan_from_admitted` 使用精确准入输入进入同一计划关系；既有 typed builder 的摘要规则与旧 schema 不变。本 seed-2 当前源码闭包同时绑定该修复，不回写 seed-1 或历史差异材料。
公共 `oac verify`、`oac lower`、`oac validate --verify-digest` 与 `oac digest` 也保留输入 JSON 的成员存在性；stdio-v3 与 CLI 共用精确准入入口。typed library builder、已发布 TCK 与 benchmark 历史输入语义保留，不把旧报告重新计算成当前资格。

本轮另补 Spec 008 本地 E0a 材料入口：独立运输 schema + 纯 intake parser、外部 pin 权限记录、真实 extension bytes/引用闭合和 Supplier 样例，复用既有 SourceAdmissionReceipt 与唯一 raw-map Demand/Supplier 关系。八轴只读投影不产生执行或经验准入权限。seed-2 的当前源码闭包绑定此实现，原 Kind/根算法、seed-1 材料及旧 task checkbox 不变。


## Integrated-source successor

The next current source gate targets `oac.evolution.minimum/v0.1-seed-3`; its final manifest is published
only after all intended source/schema/test/documentation changes have been integrated. The
[publication procedure](../../docs/validation/EVOLUTION-SEED3-PUBLICATION.md) retains the 41 genuine
seed-2 materials and a byte-identical copy of its manifest, recursively verifies the seed-1 lineage,
and inventories all current Python modules including nested packages. The checker never compares
new source bytes to the old seed-2 source closure or skips newly added modules. Format v0alpha3 adds
captured-manifest and ancestor evidence without expanding the minimum-profile claim ceiling.

T009-011 and T009-012 remain evidence-gated integration tasks. Their completion must be recorded from
actual final combined checks, rather than inferred from the successor publication mechanism alone.
