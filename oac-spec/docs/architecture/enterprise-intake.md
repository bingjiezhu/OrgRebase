# 企业材料准入：有界 Supplier profile

本模块完成 Spec 008 的本地 E0a 材料入口：对一个精确材料包进行解析、规则和权限范围验证，生成既有 Spec 009 `SourceAdmissionReceipt`；只有完整准入后才调用现有 Supplier 派生器。它不执行工具、不联网、不修改企业事实，也不把材料中自称的人类身份当作企业 IAM 证明。

入口为 `oac.enterprise_intake.admit_intake`，输入包括原始 manifest、按 sourceRef 索引的原始 JSON bytes、规则和权限文档、由调用方独立提供的规则/权限 ResourceRef，以及显式 evaluated_at。候选材料不能自行选定可信公钥、权限记录或当前时间。`parse_intake_manifest` 只做封闭结构解析，不产生准入权限。

```python
from oac.enterprise_intake import admit_intake

result = admit_intake(
    manifest_bytes,
    material_bytes_by_source_ref,
    profile_bytes,
    authority_bytes,
    profile_ref=trusted_profile_ref,
    authority_ref=trusted_authority_ref,
    evaluated_at=evaluation_time,
)
```

`IntakeAdmissionResult` 包含 manifest_digest、transport_digest、既有准入 receipt、source_root、demand_root、derived_contract、evidence_class。拒绝或未知结果不返回根或派生合同。无效结构、字节/身份不一致、失效或越界权限直接拒绝，不制造已授权回执。

## 输入和身份

- `oac.enterprise-intake/v0.1` manifest 的资源清单必须与提供的 bytes 清单完全相等；sourceRef 和资源身份均不重复。
- packageId 仅用于传输定位，从 logical manifest 摘要投影排除；transport_digest 单独记录完整原始 manifest bytes。更换 packageId 不改变权限所绑定的逻辑材料、回执或 S/D 根；资源清单顺序、owner、scope 等真实逻辑输入保持可观察。
- rawDigest 绑定原始运输 bytes；resourceRef.digest 绑定保留成员存在性的原始 JSON map。更换空格可能保持语义摘要，但仍改变运输摘要；二者不能互相替代。
- 文档严格 UTF-8、重复键拒绝、I-JSON、单材料最多 1 MiB、JSON 深度最多 64（根为 1）、manifest 最多 128 个资源、整个输入最多 16 MiB。未知映射也先过相同资源边界。
- 规则、权限和 extension 使用 `oac.enterprise-intake.document/v1` 封闭文档；它们不是新增 OAC Kind。独立 schema 在 `schemas/enterprise-intake/v1`，由现有 schema exporter 生成，旧 core schemas 不变。
- 规则明确允许的 extension kind、用途与范围；未知材料保持 unresolved，不能猜测映射。statement 必须有实际非空白内容；文本内容不会自动成为业务事实。

## 规则和权限

公开 `intake_admission_rules()` 定义独立的 `oac.enterprise-intake.admission-rules/v1` 规则投影；`intake_rule_set_digest()` 计算其完整 JCS 摘要。Profile 必须显式选择该规则摘要，receipt.ruleSetDigest 记录规则摘要，intakeProfileRef.digest 记录具体配置摘要。二者不可互换；旧或伪造规则坐标会被拒绝。导出规则为 `schemas/enterprise-intake/v1/admission-rules.json`。

权限记录必须同时绑定 exact profileRef、manifestDigest、完整 subjectRefs、scopeRefs、issuedAt/expiresAt。审核与决策者必须具有记录中的相应动作、声明为 HUMAN，并与所有 proposer 不同。AI、AI_CONTROLLED、生产者自审、同 principal 的不同 revision/digest 别名均被拒绝。资源、规则、权限、extension 的 ownerRef 必须在已准入 Snapshot 中解析为已准入角色；不要求不同责任范围共享同一个 owner。

`SCRIPTED_LOCAL` 表示可复验的本地治理输入。`EXTERNAL_ASSERTION` 仍仅表示调用方提供了独立 pin，不能据此宣称已核验外部身份、密钥托管或实时撤销。真正集成方负责 pin 的可信取得、身份映射与时钟来源。

## 同一业务关系

Supplier profile 要求一份 OrganizationSnapshot、一份 OrganizationalDemand 和一份 SemanticChangeSet。Demand requester/role/namespace/governance 通过同一 `verify_organizational_demand_from_admitted` 验证。scope 必须对应真实 Snapshot 节点；主体、结果标准、证据义务、约束和补充触发都要解析到精确 extension bytes，Change trigger 绑定精确 Change ref。

SourceAdmissionReceipt 只准入资源 envelope。它不能把 nested candidate fact 或 candidate Change 直接提升成权威；实际 Supplier 派生仍执行原有资格判断。准入成功才计算既有 S/D 根并调用 `derive_supplier_contract_from_admitted`。没有新增编排器、编译器或权限旁路。

八轴只读投影见 [状态视图契约](enterprise-intake-state.md)。A1–A5 来自实际证据重新验证；A6/A7/A8 的 E0a 边界不伪造执行、结果 oracle 或经验准入。

## 固定样例与验证

`profiles/enterprise-intake/supplier-review-v0.1` 是带实际 bytes、独立 caller pins 和明确本地时间的 Supplier 样例。五项义务、角色和目标的期望来自明确业务断言；源数据沿用已有 Supplier 示例，不把新观察复制为 Gold。`tests/test_enterprise_intake.py` 覆盖整个材料入口、真实派生、八轴同根、摘要/权限/角色/范围/资源上限以及未知语义；`tests/test_intake_state.py` 覆盖证据重绑定和零效果边界。

最终 `make archive-replay-check` 会执行这些测试、类型检查与 schema drift 门；独立安装验收使用同一公开函数和已打包样例。实际运行日志和字节清单位于本轮 `运行记录/20260909-product-completion/oac-successor`，阶段通过不替代最终干净归档与安装结果。
